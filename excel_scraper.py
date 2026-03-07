#!/usr/bin/env python3
"""
excel_scraper.py — Standalone Excel scraping from Damodaran's fcffsimpleginzu.xlsx

Source: https://pages.stern.nyu.edu/~adamodar/pc/fcffsimpleginzu.xlsx

Sheets scraped (all prefixed xl_ to distinguish from web-scraped tables):
  [EXCEL] Country Equity Risk Premiums   → xl_country_equity_risk_premium
  [EXCEL] Industry Averages (US)         → xl_industry_averages_us
  [EXCEL] Industry Averages (Global)     → xl_industry_averages_global
  [EXCEL] Input Stat Distributions       → xl_input_stats
  [EXCEL] R&D Amortizable Lives          → xl_rd_amortizable_lives
  [EXCEL] Synthetic Rating (large/small) → xl_synthetic_rating_large_firm
                                            xl_synthetic_rating_small_firm
  [EXCEL] Cost of Capital Histogram      → xl_cost_of_capital_histogram
  Note: For Input Stat the following industries are missing:
  Bank (Money Center), Banks (Regional), Brokerage & Investment Banking, Financial Svcs. (Non-bank & Insurance), Insurance (General), Insurance (Life), Insurance (Prop/Cas.), Investments & Asset Management, Reinsurance

Run standalone (no Flask needed):
    python3 excel_scraper.py               # parse and print summary, no DB write
    python3 excel_scraper.py --write-db    # parse and write to PostgreSQL
"""

import logging
import sys
import time
from difflib import SequenceMatcher
from functools import wraps
from io import BytesIO
from typing import Any, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FCFF_EXCEL_URL = 'https://pages.stern.nyu.edu/~adamodar/pc/fcffsimpleginzu.xlsx'
REQUEST_TIMEOUT = 60   # seconds — large file
MAX_RETRIES = 3
RETRY_DELAY = 2        # seconds

# Column indices (0-based) in the Input Stat Distributions sheet whose values
# are stored as decimal fractions (e.g. 0.059 = 5.9 %) — multiplied ×100 on ingest.
# Ratio columns (sales_to_invested_capital indices 8-10, beta indices 14-16) are kept as-is.
_INPUT_STATS_PERCENT_INDICES = frozenset({
    2, 3, 4,    # revenue_growth_rate       Q1 / median / Q3
    5, 6, 7,    # pre_tax_operating_margin  Q1 / median / Q3
    11, 12, 13, # cost_of_capital           Q1 / median / Q3
    17, 18, 19, # debt_to_capital_ratio     Q1 / median / Q3
})

# ── Validation constants ───────────────────────────────────────────────────────
#
# Expected primary keys per table.  Validated on every scrape run so that any
# structural change in the source Excel (renamed industry, removed region, etc.)
# is caught immediately and surfaced as a clear error in the printed summary.
#
# For large industry tables a *representative subset* is used rather than the
# full list, because Damodaran reclassifies and renames industries in annual
# updates.  The subset contains core industries that have been stable for many
# years.  The complete set for small/fully-enumerable tables is listed.
#
# For xl_synthetic_rating_* the PK is column index 2 (rating), not 0.
_EXPECTED_PKS: dict = {
    # All 5 regions — fully enumerable, never changes
    'xl_cost_of_capital_histogram': frozenset({
        'Emerging', 'Europe', 'Global', 'Japan', 'US',
    }),
    # All 15 ratings — fully enumerable
    'xl_synthetic_rating_large_firm': frozenset({
        'Aaa/AAA', 'Aa2/AA', 'A1/A+', 'A2/A', 'A3/A-',
        'Baa2/BBB', 'Ba1/BB+', 'Ba2/BB',
        'B1/B+', 'B2/B', 'B3/B-',
        'Caa/CCC', 'Ca2/CC', 'C2/C', 'D2/D',
    }),
    'xl_synthetic_rating_small_firm': frozenset({
        'Aaa/AAA', 'Aa2/AA', 'A1/A+', 'A2/A', 'A3/A-',
        'Baa2/BBB', 'Ba1/BB+', 'Ba2/BB',
        'B1/B+', 'B2/B', 'B3/B-',
        'Caa/CCC', 'Ca2/CC', 'C2/C', 'D2/D',
    }),
    # Stable core industries present in every annual update of the US sheet,
    # including the two aggregate rows and two financial categories that are
    # absent from xl_input_stats but must appear here.
    'xl_industry_averages_us': frozenset({
        'Advertising', 'Aerospace/Defense', 'Air Transport',
        'Food Processing', 'Hotel/Gaming', 'Machinery',
        'Oil/Gas (Integrated)', 'R.E.I.T.', 'Semiconductor', 'Steel',
        'Telecom. Services', 'Tobacco', 'Utility (General)',
        'Total Market', 'Total Market (without financials)',
        'Banks (Regional)', 'Insurance (General)',
    }),
    # Same core set for global (financials may be absent — intentional)
    'xl_industry_averages_global': frozenset({
        'Advertising', 'Aerospace/Defense', 'Air Transport',
        'Food Processing', 'Hotel/Gaming', 'Machinery',
        'Oil/Gas (Integrated)', 'R.E.I.T.', 'Semiconductor', 'Steel',
        'Telecom. Services', 'Tobacco', 'Utility (General)',
        'Total Market',
    }),
    # Stable non-financial core industries (financials are intentionally absent)
    'xl_input_stats': frozenset({
        'Advertising', 'Aerospace/Defense', 'Air Transport',
        'Food Processing', 'Hotel/Gaming', 'Machinery',
        'Oil/Gas (Integrated)', 'R.E.I.T.', 'Semiconductor', 'Steel',
        'Telecom. Services', 'Tobacco', 'Utility (General)',
    }),
}

# Minimum row counts — scraper result is flagged if it falls below these.
_MIN_ROW_COUNTS: dict = {
    'xl_country_equity_risk_premium':  150,
    'xl_industry_averages_us':          80,
    'xl_industry_averages_global':      60,
    'xl_input_stats':                   70,
    'xl_rd_amortizable_lives':          50,
    'xl_synthetic_rating_large_firm':   10,
    'xl_synthetic_rating_small_firm':   10,
    'xl_cost_of_capital_histogram':      5,
}

# Module-level download cache — download once per script run
_xl_cache: Optional[BytesIO] = None


# ── Retry decorator ───────────────────────────────────────────────────────────

def _retry(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    """Decorator: retry with exponential backoff on any exception."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed in {func.__name__}: {e}. "
                        f"Retrying in {wait}s…"
                    )
                    time.sleep(wait)
        return wrapper
    return decorator


# ── Download (cached) ─────────────────────────────────────────────────────────

@_retry()
def _download_excel() -> BytesIO:
    """Download fcffsimpleginzu.xlsx once and cache it for the session."""
    global _xl_cache
    if _xl_cache is not None:
        _xl_cache.seek(0)
        return _xl_cache
    logger.info(f"Downloading {FCFF_EXCEL_URL}")
    r = requests.get(FCFF_EXCEL_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    _xl_cache = BytesIO(r.content)
    logger.info(f"  Downloaded {len(r.content) / 1024:.0f} KB")
    return _xl_cache


def _get_sheet_names() -> List[str]:
    buf = _download_excel()
    buf.seek(0)
    return pd.ExcelFile(buf, engine='openpyxl').sheet_names


def _read_sheet(sheet_name: str) -> pd.DataFrame:
    buf = _download_excel()
    buf.seek(0)
    return pd.read_excel(buf, sheet_name=sheet_name, header=None, engine='openpyxl')


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _find_sheet(keywords: List[str], exclude: Optional[List[str]] = None) -> Optional[str]:
    """
    Find a sheet whose name contains ALL keywords (case-insensitive).
    Sheets containing any exclude keyword are skipped.
    Falls back to difflib fuzzy matching on the first keyword when no exact match found.
    """
    sheet_names = _get_sheet_names()
    excl = [e.lower() for e in (exclude or [])]

    for name in sheet_names:
        n = name.lower()
        if all(kw.lower() in n for kw in keywords):
            if not any(ex in n for ex in excl):
                return name

    # Fuzzy fallback on first keyword only
    kw = keywords[0].lower()
    best, best_r = None, 0.0
    for name in sheet_names:
        r = SequenceMatcher(None, kw, name.lower()).ratio()
        if r > best_r:
            best_r, best = r, name
    if best_r >= 0.6 and best and not any(ex in best.lower() for ex in excl):
        logger.info(f"Fuzzy matched sheet '{best}' (ratio={best_r:.2f}) for '{kw}'")
        return best
    return None


def _find_header_row(df: pd.DataFrame, keyword: str, max_rows: int = 25) -> Optional[int]:
    """Return the first row index (within max_rows) where column 0 contains keyword."""
    kw = keyword.lower()
    for i in range(min(max_rows, len(df))):
        if kw in str(df.iloc[i, 0]).strip().lower():
            return i
    return None


def _find_label_row(
    df: pd.DataFrame,
    keyword: str,
    col: int = 0,
    start: int = 0,
    max_rows: int = 200,
) -> Optional[int]:
    """Return the first row index containing keyword in column `col`, starting from `start`."""
    kw = keyword.lower()
    end = min(start + max_rows, len(df))
    for i in range(start, end):
        if kw in str(df.iloc[i, col]).strip().lower():
            return i
    return None


def _parse_int(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None
    try:
        return int(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if pd.isna(value):
        return None
    try:
        s = str(value).replace('%', '').replace(',', '').strip()
        if not s or s.lower() in ('nan', 'n/a', 'na', '#n/a', '#div/0!', '#value!'):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _clean(s: Any) -> Any:
    """Collapse internal whitespace in a string."""
    if isinstance(s, str):
        return ' '.join(s.split())
    return s


def _is_empty(value: Any) -> bool:
    """True if value is NaN or blank string."""
    if pd.isna(value):
        return True
    return str(value).strip().lower() in ('', 'nan', 'none')


def _validate_rows(table_name: str, rows: list, pk_index: int = 0) -> List[str]:
    """
    Validate scraped rows against _MIN_ROW_COUNTS and _EXPECTED_PKS.
    Returns a list of human-readable error strings (empty = all OK).
    pk_index: position of the primary key in each row tuple (default 0).
    """
    errors: List[str] = []

    min_count = _MIN_ROW_COUNTS.get(table_name)
    if min_count is not None and len(rows) < min_count:
        errors.append(f"ROW COUNT too low: got {len(rows)}, expected >= {min_count}")

    expected = _EXPECTED_PKS.get(table_name)
    if expected:
        actual = {row[pk_index] for row in rows}
        missing = expected - actual
        if missing:
            errors.append(f"MISSING PKs ({len(missing)}): {sorted(missing)}")

    return errors


# ── Scraper: Country Equity Risk Premium ─────────────────────────────────────

@_retry()
def scrape_xl_country_equity_risk_premium() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] Country equity risk premiums from fcffsimpleginzu.xlsx.
    Sheet: 'Country equity risk premiums'
    Table: xl_country_equity_risk_premium
    Columns (7): country, moody_rating, adj_default_spread, equity_risk_premium,
                 country_risk_premium, corporate_tax_rate, mature_market_erp
    Values are stored as decimal fractions (e.g. 0.0487 = 4.87 %).
    Header at row 3; data from row 4.
    mature_market_erp is a sheet-level scalar (same value for every row) read from
    the preamble rows above the country table, labelled "Mature Market ERP" (or with
    a trailing "+").
    """
    try:
        sheet = (
            _find_sheet(['country equity']) or
            _find_sheet(['country'], exclude=['averages', 'industry']) or
            _find_sheet(['ctry'], exclude=['industry'])
        )
        if sheet is None:
            return None, f"Could not find Country ERP sheet. Available sheets: {_get_sheet_names()}"
        logger.info(f"[xl_country_equity_risk_premium] Using sheet: '{sheet}'")

        df = _read_sheet(sheet)

        # Find the header row where col 0 is exactly 'Country' (case-insensitive).
        # We use exact match to avoid matching rows that merely mention 'country'
        # in a longer description (e.g. "Changing this number will update all your
        # country equity risk premiums.").
        h = None
        for i in range(min(15, len(df))):
            if str(df.iloc[i, 0]).strip().lower() == 'country':
                h = i
                break
        if h is None:
            return None, "Could not find 'Country' header row"

        # Extract the sheet-level Mature Market ERP scalar from the preamble rows
        # (rows 0..h-1).  The label is "Mature Market ERP" (sometimes with a trailing
        # "+").  The numeric value sits in the next non-empty cell on the same row.
        mature_market_erp: Optional[float] = None
        for i in range(h):
            for j in range(min(len(df.columns) - 1, 6)):
                cell = str(df.iloc[i, j]).strip().rstrip('+').strip()
                if cell.lower() == 'mature market erp':
                    for k in range(j + 1, min(j + 4, len(df.columns))):
                        v = _parse_float(df.iloc[i, k])
                        if v is not None:
                            mature_market_erp = v
                            break
                if mature_market_erp is not None:
                    break
            if mature_market_erp is not None:
                break

        if mature_market_erp is None:
            logger.warning("[xl_country_equity_risk_premium] Could not find Mature Market ERP in preamble rows")

        df_data = df.iloc[h + 1:].reset_index(drop=True)

        # Drop rows where country column is empty
        df_data = df_data[~df_data.iloc[:, 0].apply(_is_empty)]

        if len(df_data.columns) < 6:
            return None, f"Expected ≥6 columns, got {len(df_data.columns)}"

        rows = []
        seen: set = set()
        for _, row in df_data.iterrows():
            country = _clean(str(row.iloc[0]).strip())
            if country in seen:
                continue
            seen.add(country)
            rows.append((
                country,
                str(row.iloc[1]).strip() if not _is_empty(row.iloc[1]) else None,  # moody_rating
                _parse_float(row.iloc[2]),  # adj_default_spread
                _parse_float(row.iloc[3]),  # equity_risk_premium
                _parse_float(row.iloc[4]),  # country_risk_premium
                _parse_float(row.iloc[5]),  # corporate_tax_rate
                mature_market_erp,          # mature_market_erp (sheet-level scalar)
            ))

        if len(rows) < 50:
            return None, f"Only {len(rows)} rows (expected ≥50). Sheet structure may have changed."
        logger.info(f"[xl_country_equity_risk_premium] {len(rows)} rows")
        return rows, None

    except Exception as e:
        return None, str(e)


# ── Scraper: Industry Averages (shared logic) ─────────────────────────────────
#
# Exact column order from the Excel header row (verified against the file):
#  0  industry_name              → industry (TEXT PK)
#  1  number_of_firms            → num_firms (INTEGER)
#  2  revenue_growth_rate_5y     Annual Average Revenue growth - Last 5 years
#  3  pretax_operating_margin    Pre-tax Operating Margin (Unadjusted)
#  4  aftertax_roc               After-tax ROC
#  5  effective_tax_rate         Average effective tax rate
#  6  unlevered_beta             Unlevered Beta
#  7  levered_beta               Equity (Levered) Beta
#  8  cost_of_equity             Cost of equity
#  9  std_dev_stock_price        Std deviation in stock prices
# 10  pretax_cost_of_debt        Pre-tax cost of debt
# 11  market_debt_to_capital     Market Debt/Capital
# 12  cost_of_capital            Cost of capital
# 13  sales_to_capital           Sales/Capital
# 14  ev_to_sales                EV/Sales
# 15  ev_to_ebitda               EV/EBITDA
# 16  ev_to_ebit                 EV/EBIT
# 17  price_to_book              Price/Book
# 18  trailing_pe                Trailing PE
# 19  noncash_wc_pct_revenue     Non-cash WC as % of Revenues
# 20  capex_pct_revenue          Cap Ex as % of Revenues
# 21  net_capex_pct_revenue      Net Cap Ex as % of Revenues
# 22  reinvestment_rate          Reinvestment Rate
# 23  roe                        ROE
# 24  dividend_payout_ratio      Dividend Payout Ratio
# 25  equity_reinvestment_rate   Equity Reinvestment Rate
# 26  pretax_operating_margin_adj Pre-tax Operating Margin (Lease & R&D adjusted)

_INDUSTRY_AVG_NCOLS = 27  # total columns including industry + num_firms


def _parse_industry_averages(sheet_name: str, label: str) -> Tuple[Optional[list], Optional[str]]:
    """
    Generic parser for Industry Averages sheets.
    Returns list of 27-element tuples matching xl_industry_averages_us/global schema:
    (industry, num_firms, revenue_growth_rate_5y, pretax_operating_margin, ...,
     pretax_operating_margin_adj)
    All metric columns (indices 2-26) are stored as REAL decimal fractions.
    """
    try:
        df = _read_sheet(sheet_name)
        h = _find_header_row(df, 'industry')
        if h is None:
            return None, f"[{label}] Could not find 'Industry' header row"

        df_data = df.iloc[h + 1:].reset_index(drop=True)

        # Drop rows where industry (col 0) is empty
        df_data = df_data[~df_data.iloc[:, 0].apply(_is_empty)]

        if len(df_data.columns) < _INDUSTRY_AVG_NCOLS:
            return None, (
                f"[{label}] Expected ≥{_INDUSTRY_AVG_NCOLS} columns, "
                f"got {len(df_data.columns)}"
            )

        rows = []
        seen: set = set()
        for _, row in df_data.iterrows():
            industry = _clean(str(row.iloc[0]).strip())
            if industry in seen:
                continue
            seen.add(industry)
            num_firms = _parse_int(row.iloc[1])
            metrics = [_parse_float(row.iloc[i]) for i in range(2, _INDUSTRY_AVG_NCOLS)]
            rows.append((industry, num_firms, *metrics))

        if len(rows) < 20:
            return None, f"[{label}] Only {len(rows)} rows (expected ≥20)"
        logger.info(f"[{label}] {len(rows)} rows")
        return rows, None

    except Exception as e:
        return None, str(e)


_INDUSTRY_AVG_INSERT = (
    'INSERT INTO {table} VALUES ('
    + ','.join(['%s'] * _INDUSTRY_AVG_NCOLS)
    + ')'
)


@_retry()
def scrape_xl_industry_averages_us() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] US industry averages from fcffsimpleginzu.xlsx.
    Sheet: 'Industry Averages(US)'
    Table: xl_industry_averages_us
    27 named columns — see column map above _parse_industry_averages.
    All metric values are decimal fractions (e.g. 0.078 = 7.8 %).
    """
    sheet = (
        _find_sheet(['industry', 'us'], exclude=['global']) or
        _find_sheet(['us industry']) or
        _find_sheet(['industry'], exclude=['global'])
    )
    if sheet is None:
        return None, f"Could not find US Industry Averages sheet. Available: {_get_sheet_names()}"
    logger.info(f"[xl_industry_averages_us] Using sheet: '{sheet}'")
    return _parse_industry_averages(sheet, 'xl_industry_averages_us')


@_retry()
def scrape_xl_industry_averages_global() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] Global industry averages from fcffsimpleginzu.xlsx.
    Sheet: 'Industry Averages (Global)'
    Table: xl_industry_averages_global
    27 named columns — see column map above _parse_industry_averages.
    All metric values are decimal fractions (e.g. 0.078 = 7.8 %).
    """
    sheet = (
        _find_sheet(['industry', 'global']) or
        _find_sheet(['global industry'])
    )
    if sheet is None:
        return None, f"Could not find Global Industry Averages sheet. Available: {_get_sheet_names()}"
    logger.info(f"[xl_industry_averages_global] Using sheet: '{sheet}'")
    return _parse_industry_averages(sheet, 'xl_industry_averages_global')


# ── Scraper: Input Stat Distributions ────────────────────────────────────────

@_retry()
def scrape_xl_input_stats() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] Input stat distributions from fcffsimpleginzu.xlsx.
    Table: xl_input_stats
    Columns (20): industry (PK), count,
                  revenue_growth_rate Q1/median/Q3,
                  pre_tax_operating_margin Q1/median/Q3,
                  sales_to_invested_capital Q1/median/Q3,
                  cost_of_capital Q1/median/Q3,
                  beta Q1/median/Q3,
                  debt_to_capital_ratio Q1/median/Q3

    Note: decimal-fraction percent columns (indices in _INPUT_STATS_PERCENT_INDICES)
    are multiplied ×100 before storage. Ratio columns (sales/capital, beta) are kept as-is.

    Financial industries (banks, insurance, etc.) are absent from the Global dataset —
    this is expected and not an error.
    """
    EXPECTED_COLS = 20
    MIN_ROWS = 40

    try:
        sheet_names = _get_sheet_names()

        # Try substring keywords first, then fuzzy
        sheet = None
        for kw in ('input stat distribut', 'input stat', 'stat distribut'):
            sheet = next((n for n in sheet_names if kw in n.lower()), None)
            if sheet:
                break
        if sheet is None:
            best, best_r = None, 0.0
            for n in sheet_names:
                r = SequenceMatcher(None, 'input stat distribut', n.lower()).ratio()
                if r > best_r:
                    best_r, best = r, n
            if best_r >= 0.6:
                sheet = best
                logger.info(f"Fuzzy matched sheet '{sheet}' (ratio={best_r:.2f})")
        if sheet is None:
            return None, f"Could not find Input Stat Distributions sheet in: {sheet_names}"

        logger.info(f"[xl_input_stats] Using sheet: '{sheet}'")
        df = _read_sheet(sheet)

        h = _find_header_row(df, 'industry')
        if h is None:
            return None, "Could not find 'Industry' header row"

        df_data = df.iloc[h + 1:].reset_index(drop=True)
        if len(df_data.columns) < EXPECTED_COLS:
            return None, f"Expected ≥{EXPECTED_COLS} columns, got {len(df_data.columns)}"
        df_data = df_data.iloc[:, :EXPECTED_COLS]

        df_data = df_data[~df_data.iloc[:, 0].apply(_is_empty)]

        rows = []
        seen: set = set()
        skipped = 0
        for _, row in df_data.iterrows():
            cleaned = []
            for i, val in enumerate(row):
                if i == 0:
                    cleaned.append(_clean(str(val).strip()))
                elif i == 1:
                    cleaned.append(_parse_int(val))
                elif i in _INPUT_STATS_PERCENT_INDICES:
                    f = _parse_float(val)
                    cleaned.append(round(f * 100, 2) if f is not None else None)
                else:
                    cleaned.append(_parse_float(val))

            industry = cleaned[0]
            if industry in seen:
                skipped += 1
                continue
            seen.add(industry)
            rows.append(tuple(cleaned))

        if skipped:
            logger.info(f"[xl_input_stats] Deduplicated {skipped} duplicate rows")
        if len(rows) < MIN_ROWS:
            return None, f"Only {len(rows)} rows (expected ≥{MIN_ROWS})"
        logger.info(f"[xl_input_stats] {len(rows)} rows")
        return rows, None

    except Exception as e:
        return None, str(e)


# ── Scraper: R&D Amortizable Lives ────────────────────────────────────────────

@_retry()
def scrape_xl_rd_amortizable_lives() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] R&D converter lookup table for amortizable lives.
    Sheet: 'R& D converter'
    Table: xl_rd_amortizable_lives
    Columns: business_type (PK TEXT), amortizable_life_years (REAL)
    The lookup table section starts with 'Lookup Table for Amortizable Lives'
    followed by a header row 'Industry Name | Amortization Period'.
    Data rows begin two rows after that label.
    """
    try:
        sheet = (
            _find_sheet(['converter']) or
            _find_sheet(['r&d']) or
            _find_sheet(['r& d'])
        )
        if sheet is None:
            return None, f"Could not find R&D Converter sheet. Available: {_get_sheet_names()}"
        logger.info(f"[xl_rd_amortizable_lives] Using sheet: '{sheet}'")

        df = _read_sheet(sheet)

        # Find the 'Lookup Table for Amortizable Lives' section label
        label_row = (
            _find_label_row(df, 'lookup table', max_rows=200) or
            _find_label_row(df, 'amortizable lives', max_rows=200)
        )
        if label_row is None:
            return None, "Could not find 'Lookup Table for Amortizable Lives' section"

        # label_row   → section title ("Lookup Table for Amortizable Lives")
        # label_row+1 → column header ("Industry Name" | "Amortization Period")
        # label_row+2 → first data row
        start_row = label_row + 2
        df_data = df.iloc[start_row:].reset_index(drop=True)

        rows = []
        seen: set = set()
        for _, row in df_data.iterrows():
            name_val = str(row.iloc[0]).strip() if not _is_empty(row.iloc[0]) else None
            if name_val is None:
                break  # data is contiguous; stop at first blank row
            life = _parse_float(row.iloc[1]) if len(row) > 1 else None
            if life is None:
                continue
            btype = _clean(name_val)
            if btype in seen:
                continue
            seen.add(btype)
            rows.append((btype, life))

        if len(rows) < 3:
            return None, f"Only {len(rows)} R&D amortizable life rows (expected ≥3)"
        logger.info(f"[xl_rd_amortizable_lives] {len(rows)} rows")
        return rows, None

    except Exception as e:
        return None, str(e)


# ── Scraper: Synthetic Rating ─────────────────────────────────────────────────

@_retry()
def scrape_xl_synthetic_rating() -> Tuple[Optional[list], Optional[list], Optional[str]]:
    """
    [EXCEL] Synthetic rating lookup table — large manufacturing firms and
    smaller/riskier firms.
    Tables: xl_synthetic_rating_large_firm, xl_synthetic_rating_small_firm
    Columns each: min_coverage (TEXT), max_coverage (TEXT), rating (TEXT PK), spread (TEXT)

    Returns: (large_tuples, small_tuples, None) on success
             (None, None, error_message) on failure
    """
    try:
        sheet = (
            _find_sheet(['synthetic']) or
            _find_sheet(['ratings'], exclude=['country', 'erp', 'ctry']) or
            _find_sheet(['rating lookup'])
        )
        if sheet is None:
            return None, None, f"Could not find Synthetic Rating sheet. Available: {_get_sheet_names()}"
        logger.info(f"[xl_synthetic_rating] Using sheet: '{sheet}'")

        df = _read_sheet(sheet)

        def _parse_section(start: int, end: int) -> List[tuple]:
            """
            Parse a rating section (rows start..end).
            Each data row yields (min_coverage, max_coverage, rating, spread) as TEXT.
            Data rows are identified by col 0 being parseable as a float (skips header rows).
            Only columns 0-3 are used; additional lookup columns on the right are ignored.
            """
            result = []
            for i in range(start, min(end, len(df))):
                # Skip header/label rows — data rows have a numeric value in col 0
                if _parse_float(df.iloc[i, 0]) is None:
                    continue
                vals = [str(df.iloc[i, j]).strip() for j in range(4)]
                result.append((
                    vals[0] if vals[0] not in ('nan', '') else None,  # min_coverage
                    vals[1] if vals[1] not in ('nan', '') else None,  # max_coverage
                    vals[2] if vals[2] not in ('nan', '') else None,  # rating
                    vals[3] if vals[3] not in ('nan', '') else None,  # spread
                ))
            return result

        # Locate large-firm section
        large_row = (
            _find_label_row(df, 'large', max_rows=100) or
            _find_label_row(df, 'manufacturing', max_rows=100)
        )
        if large_row is None:
            large_row = 0

        # Locate small-firm section (search after large section)
        small_row = (
            _find_label_row(df, 'small', start=large_row + 1, max_rows=150) or
            _find_label_row(df, 'riskier', start=large_row + 1, max_rows=150) or
            _find_label_row(df, 'risky', start=large_row + 1, max_rows=150)
        )
        if small_row is None:
            return None, None, (
                "Could not find small/riskier firm section in synthetic rating sheet. "
                "Sheet structure may have changed."
            )

        large_data = _parse_section(large_row + 1, small_row)
        small_data = _parse_section(small_row + 1, len(df))

        if len(large_data) < 5:
            return None, None, f"Only {len(large_data)} large-firm rating rows (expected ≥5)"
        if len(small_data) < 5:
            return None, None, f"Only {len(small_data)} small-firm rating rows (expected ≥5)"

        logger.info(f"[xl_synthetic_rating] Large: {len(large_data)} rows, Small: {len(small_data)} rows")
        return large_data, small_data, None

    except Exception as e:
        return None, None, str(e)


# ── Scraper: Cost of Capital Histogram ───────────────────────────────────────

@_retry()
def scrape_xl_cost_of_capital_histogram() -> Tuple[Optional[list], Optional[str]]:
    """
    [EXCEL] Cost of capital distribution for publicly traded firms by region.
    Found within the 'Cost of capital worksheet', referenced as
    'Approach 3: Use histogram of costs of capital of all publicly traded firms'.

    Table: xl_cost_of_capital_histogram
    Columns: region (PK TEXT), first_decile (REAL), first_quartile (REAL),
             median (REAL), third_quartile (REAL), ninth_decile (REAL)
    Values are decimal fractions (e.g. 0.0779 = 7.79 %).
    Rows: Emerging, Europe, Global, Japan, US (and any others added later).
    """
    try:
        sheet = (
            _find_sheet(['cost of capital']) or
            _find_sheet(['coc']) or
            _find_sheet(['wacc'])
        )
        if sheet is None:
            return None, f"Could not find Cost of Capital sheet. Available: {_get_sheet_names()}"
        logger.info(f"[xl_cost_of_capital_histogram] Using sheet: '{sheet}'")

        df = _read_sheet(sheet)

        # Locate the header row for the distribution table (contains 'Region')
        # It appears just after "Approach 3: Use histogram of costs of capital…"
        approach_row = _find_label_row(df, 'approach 3', max_rows=200)
        search_start = approach_row if approach_row is not None else 0

        header_row = _find_label_row(df, 'region', col=0, start=search_start, max_rows=20)
        if header_row is None:
            return None, "Could not find 'Region' header row in Cost of Capital sheet"

        # Data rows follow directly after the header
        rows = []
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            region = str(row.iloc[0]).strip() if not _is_empty(row.iloc[0]) else None
            if region is None:
                break  # rows are contiguous; stop at first blank
            if len(row) < 6:
                continue
            # Stop if first numeric column is not parseable (e.g. footnote text row)
            if _parse_float(row.iloc[1]) is None:
                break
            rows.append((
                _clean(region),
                _parse_float(row.iloc[1]),  # first_decile
                _parse_float(row.iloc[2]),  # first_quartile
                _parse_float(row.iloc[3]),  # median
                _parse_float(row.iloc[4]),  # third_quartile
                _parse_float(row.iloc[5]),  # ninth_decile
            ))

        if len(rows) < 2:
            return None, f"Only {len(rows)} region rows (expected ≥2)"
        logger.info(f"[xl_cost_of_capital_histogram] {len(rows)} region rows")
        return rows, None

    except Exception as e:
        return None, str(e)


# ── Table creation SQL (CREATE TABLE IF NOT EXISTS) ───────────────────────────

_INDUSTRY_AVG_COLS = """
    industry TEXT PRIMARY KEY,
    num_firms INTEGER,
    revenue_growth_rate_5y REAL,
    pretax_operating_margin REAL,
    aftertax_roc REAL,
    effective_tax_rate REAL,
    unlevered_beta REAL,
    levered_beta REAL,
    cost_of_equity REAL,
    std_dev_stock_price REAL,
    pretax_cost_of_debt REAL,
    market_debt_to_capital REAL,
    cost_of_capital REAL,
    sales_to_capital REAL,
    ev_to_sales REAL,
    ev_to_ebitda REAL,
    ev_to_ebit REAL,
    price_to_book REAL,
    trailing_pe REAL,
    noncash_wc_pct_revenue REAL,
    capex_pct_revenue REAL,
    net_capex_pct_revenue REAL,
    reinvestment_rate REAL,
    roe REAL,
    dividend_payout_ratio REAL,
    equity_reinvestment_rate REAL,
    pretax_operating_margin_adj REAL"""

_CREATE_TABLES_SQL = [
    "CREATE TABLE IF NOT EXISTS xl_country_equity_risk_premium ("
    "country TEXT PRIMARY KEY, moody_rating TEXT, adj_default_spread REAL,"
    " equity_risk_premium REAL, country_risk_premium REAL, corporate_tax_rate REAL,"
    " mature_market_erp REAL)",
    f"CREATE TABLE IF NOT EXISTS xl_industry_averages_us ({_INDUSTRY_AVG_COLS})",
    f"CREATE TABLE IF NOT EXISTS xl_industry_averages_global ({_INDUSTRY_AVG_COLS})",
    """CREATE TABLE IF NOT EXISTS xl_input_stats (
        industry TEXT PRIMARY KEY, count INTEGER,
        revenue_growth_rate_first_quartile REAL, revenue_growth_rate_median REAL,
        revenue_growth_rate_third_quartile REAL,
        pre_tax_operating_margin_first_quartile REAL, pre_tax_operating_margin_median REAL,
        pre_tax_operating_margin_third_quartile REAL,
        sales_to_invested_capital_first_quartile REAL, sales_to_invested_capital_median REAL,
        sales_to_invested_capital_third_quartile REAL,
        cost_of_capital_first_quartile REAL, cost_of_capital_median REAL,
        cost_of_capital_third_quartile REAL,
        beta_first_quartile REAL, beta_median REAL, beta_third_quartile REAL,
        debt_to_capital_ratio_first_quartile REAL, debt_to_capital_ratio_median REAL,
        debt_to_capital_ratio_third_quartile REAL)""",
    "CREATE TABLE IF NOT EXISTS xl_rd_amortizable_lives"
    " (business_type TEXT PRIMARY KEY, amortizable_life_years REAL)",
    "CREATE TABLE IF NOT EXISTS xl_synthetic_rating_large_firm"
    " (min_coverage TEXT, max_coverage TEXT, rating TEXT PRIMARY KEY, spread TEXT)",
    "CREATE TABLE IF NOT EXISTS xl_synthetic_rating_small_firm"
    " (min_coverage TEXT, max_coverage TEXT, rating TEXT PRIMARY KEY, spread TEXT)",
    "CREATE TABLE IF NOT EXISTS xl_cost_of_capital_histogram"
    " (region TEXT PRIMARY KEY, first_decile REAL, first_quartile REAL,"
    " median REAL, third_quartile REAL, ninth_decile REAL)",
]


def create_xl_tables(db_handler) -> None:
    """Create all xl_ tables (IF NOT EXISTS) using the provided db_handler."""
    for sql in _CREATE_TABLES_SQL:
        db_handler.execute_query(sql)
    logger.info("All xl_ tables created (or already exist)")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_xl_scraping(db_handler=None) -> dict:
    """
    Run all Excel scrapers, print a summary, and optionally write to PostgreSQL.

    Args:
        db_handler: Optional DatabaseHandler instance (from database.py).
                    If provided, tables are auto-created if needed, then refreshed
                    (DELETE + INSERT).

    Returns:
        Dict mapping table name → {'status': 'ok'|'error', 'rows': int, ...}
    """
    results: dict = {}

    if db_handler:
        create_xl_tables(db_handler)

    # ── Single-result scrapers ─────────────────────────────────────────────────
    _INPUT_STATS_PLACEHOLDERS = ','.join(['%s'] * 20)
    single_scrapers = [
        (
            'xl_country_equity_risk_premium',
            scrape_xl_country_equity_risk_premium,
            'INSERT INTO xl_country_equity_risk_premium VALUES (%s,%s,%s,%s,%s,%s,%s)',
        ),
        (
            'xl_industry_averages_us',
            scrape_xl_industry_averages_us,
            _INDUSTRY_AVG_INSERT.format(table='xl_industry_averages_us'),
        ),
        (
            'xl_industry_averages_global',
            scrape_xl_industry_averages_global,
            _INDUSTRY_AVG_INSERT.format(table='xl_industry_averages_global'),
        ),
        (
            'xl_input_stats',
            scrape_xl_input_stats,
            f'INSERT INTO xl_input_stats VALUES ({_INPUT_STATS_PLACEHOLDERS})',
        ),
        (
            'xl_rd_amortizable_lives',
            scrape_xl_rd_amortizable_lives,
            'INSERT INTO xl_rd_amortizable_lives VALUES (%s,%s)',
        ),
        (
            'xl_cost_of_capital_histogram',
            scrape_xl_cost_of_capital_histogram,
            'INSERT INTO xl_cost_of_capital_histogram VALUES (%s,%s,%s,%s,%s,%s)',
        ),
    ]

    for table_name, func, insert_query in single_scrapers:
        data, err = func()
        if err:
            results[table_name] = {'status': 'error', 'error': err}
            logger.error(f"[{table_name}] FAILED: {err}")
        else:
            val_errors = _validate_rows(table_name, data)
            results[table_name] = {'status': 'ok', 'rows': len(data), 'validation_errors': val_errors}
            for ve in val_errors:
                logger.warning(f"[{table_name}] VALIDATION: {ve}")
            if db_handler:
                try:
                    db_handler.execute_query(f'DELETE FROM {table_name}')
                    db_handler.execute_query_many(insert_query, data)
                    results[table_name]['db'] = 'written'
                    logger.info(f"[{table_name}] Written to DB ({len(data)} rows)")
                except Exception as e:
                    results[table_name]['db'] = f'error: {e}'
                    logger.error(f"[{table_name}] DB write failed: {e}")

    # ── Synthetic rating (two tables) ─────────────────────────────────────────
    large_data, small_data, err = scrape_xl_synthetic_rating()
    if err:
        results['xl_synthetic_rating_large_firm'] = {'status': 'error', 'error': err}
        results['xl_synthetic_rating_small_firm'] = {'status': 'error', 'error': err}
        logger.error(f"[xl_synthetic_rating] FAILED: {err}")
    else:
        for table_name, data in (
            ('xl_synthetic_rating_large_firm', large_data),
            ('xl_synthetic_rating_small_firm', small_data),
        ):
            val_errors = _validate_rows(table_name, data, pk_index=2)
            results[table_name] = {'status': 'ok', 'rows': len(data), 'validation_errors': val_errors}
            for ve in val_errors:
                logger.warning(f"[{table_name}] VALIDATION: {ve}")
            logger.info(f"[{table_name}] OK — {len(data)} rows")
            if db_handler:
                insert_q = f'INSERT INTO {table_name} VALUES (%s,%s,%s,%s)'
                try:
                    db_handler.execute_query(f'DELETE FROM {table_name}')
                    db_handler.execute_query_many(insert_q, data)
                    results[table_name]['db'] = 'written'
                    logger.info(f"[{table_name}] Written to DB ({len(data)} rows)")
                except Exception as e:
                    results[table_name]['db'] = f'error: {e}'
                    logger.error(f"[{table_name}] DB write failed: {e}")

    # ── Print summary ─────────────────────────────────────────────────────────
    print('\n' + '=' * 70)
    print('Excel Scraper  ·  fcffsimpleginzu.xlsx')
    print('=' * 70)
    ok_count = sum(1 for v in results.values() if v['status'] == 'ok')
    fail_count = len(results) - ok_count
    for name, r in results.items():
        if r['status'] == 'ok':
            db_note = f"  |  DB: {r.get('db', 'not written')}" if db_handler else ''
            val_flag = '  [VALIDATION FAILED]' if r.get('validation_errors') else ''
            print(f"  OK    {name}: {r['rows']} rows{db_note}{val_flag}")
        else:
            print(f"  FAIL  {name}: {r['error']}")
    print(f"\nResult: {ok_count} OK, {fail_count} failed")
    print('=' * 70)

    # ── Validation error detail ────────────────────────────────────────────────
    all_val_errors = [
        (name, ve)
        for name, r in results.items()
        for ve in r.get('validation_errors', [])
    ]
    if all_val_errors:
        affected = len({name for name, _ in all_val_errors})
        print('\n' + '!' * 70)
        print(f'VALIDATION ERRORS — {affected} table(s) have unexpected data')
        print('!' * 70)
        for name, ve in all_val_errors:
            print(f'  [{name}]')
            print(f'    {ve}')
        print('!' * 70)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    write_db = '--write-db' in sys.argv
    db_handler = None

    if write_db:
        try:
            from database import DatabaseHandler
            db_handler = DatabaseHandler()
            db_handler.connect()
            logger.info('Connected to database')
        except Exception as e:
            logger.error(f'Could not connect to database: {e}')
            sys.exit(1)

    try:
        run_all_xl_scraping(db_handler=db_handler)
    finally:
        if db_handler:
            db_handler.close()
