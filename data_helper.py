import pandas as pd
import requests
from io import StringIO
from bs4 import BeautifulSoup
from datetime import datetime
import time
import logging
from functools import wraps

# Configure logging
logger = logging.getLogger(__name__)

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
REQUEST_TIMEOUT = 30  # seconds

# Table scraping configurations
# Each entry defines how to scrape and validate a specific table from Damodaran's website
TABLE_CONFIGS = {
    # DEPRECATED: use scrape_xl_country_equity_risk_premium() from excel_scraper.py
    'country_risk_premium': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html',
        'table_index': 1,  # Second table on the page
        'expected_columns': 8,
        'expected_header_keywords': ['Country', 'Moody'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'effective_tax_rate': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/taxrate.html',
        'table_index': 0,
        'expected_columns': 11,
        'expected_header_keywords': ['Industry', 'Number of firms'],
        'skip_rows': 2,
        'min_expected_rows': 50,
    },
    'sales_to_cap_us': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html',
        'table_index': 0,
        'expected_columns': 10,
        'expected_header_keywords': ['Industry'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'beta_us': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/totalbeta.html',
        'table_index': 0,
        'expected_columns': 7,
        'expected_header_keywords': ['Industry', 'Number of firms'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'pe_ratio_us': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html',
        'table_index': 0,
        'expected_columns': 10,
        'expected_header_keywords': ['Industry'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'rev_growth_rate': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histgr.html',
        'table_index': 0,
        'expected_columns': 7,
        'expected_header_keywords': ['Industry'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'ebit_growth': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/fundgrEB.html',
        'table_index': 0,
        'expected_columns': 5,
        'expected_header_keywords': ['Industry'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
    'roic': {
        'url': 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/fundgrEB.html',
        'table_index': 0,
        'expected_columns': 5,
        'expected_header_keywords': ['Industry'],
        'skip_rows': 1,
        'min_expected_rows': 50,
    },
}


def retry_on_failure(max_retries=MAX_RETRIES, delay=RETRY_DELAY):
    """Decorator to retry functions on failure with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.error(f"Failed after {max_retries} attempts in {func.__name__}: {str(e)}")
                        raise
                    wait_time = delay * (2 ** attempt)
                    logger.warning(f"Attempt {attempt + 1} failed in {func.__name__}: {str(e)}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator


def fetch_url_with_retry(url, timeout=REQUEST_TIMEOUT):
    """Fetch URL content with timeout."""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response
    except requests.exceptions.Timeout:
        logger.error(f"Timeout while fetching {url}")
        raise Exception(f"Request timeout after {timeout} seconds")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {url}: {str(e)}")
        raise Exception(f"Failed to fetch URL: {str(e)}")


def clean_string(s):
    """Clean a string by collapsing whitespace."""
    if isinstance(s, str):
        return ' '.join(s.split())
    return s


@retry_on_failure()
def scrape_table(config_name):
    """
    Generic scraper for Damodaran HTML tables.

    Uses TABLE_CONFIGS to determine URL, expected columns, header keywords,
    and rows to skip. Validates column count and header keywords to detect
    website schema changes early.

    Returns:
        (data_tuples, None) on success
        (None, error_message) on failure
    """
    config = TABLE_CONFIGS[config_name]
    url = config['url']

    try:
        logger.info(f"Fetching data for {config_name} from {url}")
        response = fetch_url_with_retry(url)

        html_content = StringIO(response.text)
        tables = pd.read_html(html_content)

        # Validate table index exists
        if config['table_index'] >= len(tables):
            return None, (
                f"Expected at least {config['table_index'] + 1} tables on page, "
                f"found {len(tables)}. Website structure may have changed."
            )

        df = tables[config['table_index']]

        if df.empty:
            return None, "DataFrame is empty"

        # Validate column count — allow ±2 tolerance for minor website changes
        # (e.g. an extra sub-header column from a spanning header row)
        expected_cols = config['expected_columns']
        actual_cols = len(df.columns)
        if abs(actual_cols - expected_cols) > 2:
            return None, (
                f"Unexpected number of columns for {config_name}. "
                f"Expected ~{expected_cols}, got {actual_cols}. "
                f"Website schema may have changed significantly."
            )

        # Validate header keywords — search column names AND the first few
        # data rows, because multi-row HTML headers can land in either place
        # depending on how pandas parses them.
        col_text = ' '.join(str(c) for c in df.columns)
        rows_text = ' '.join(
            str(v)
            for v in df.iloc[:min(4, len(df))].values.flatten()
        )
        header_text = col_text + ' ' + rows_text
        for keyword in config.get('expected_header_keywords', []):
            if keyword.lower() not in header_text.lower():
                return None, (
                    f"Header validation failed for {config_name}: "
                    f"'{keyword}' not found in column names or first 4 rows. "
                    f"Website schema may have changed."
                )

        # Clean first column (industry/country names)
        df.iloc[:, 0] = df.iloc[:, 0].apply(clean_string)

        # Remove % signs and strip whitespace from string values
        df = df.map(lambda x: x.replace('%', '').strip() if isinstance(x, str) else x)

        # Convert to tuples, skip header rows
        data_tuples = [tuple(x) for x in df.to_numpy()]
        data_tuples = data_tuples[config['skip_rows']:]

        # Sanity check on row count
        min_rows = config.get('min_expected_rows', 10)
        if len(data_tuples) < min_rows:
            return None, (
                f"Only {len(data_tuples)} rows scraped for {config_name}, "
                f"expected at least {min_rows}. Data may be incomplete."
            )

        return data_tuples, None

    except Exception as e:
        return None, str(e)


# DEPRECATED: use scrape_xl_synthetic_rating() from excel_scraper.py
@retry_on_failure()
def clean_default_spread():
    """
    Scrape default spread data — special case because it splits into
    large firm and small firm datasets from a single table.

    Returns:
        (large_firms_data, small_firms_data, None) on success
        (None, None, error_message) on failure
    """
    try:
        url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
        logger.info(f"Fetching data for default_spread from {url}")
        response = fetch_url_with_retry(url)

        html_content = StringIO(response.text)
        tables = pd.read_html(html_content)
        df = tables[0]

        if df.empty:
            return None, None, "DataFrame is empty"

        # 9 columns: 4 for large firms, 1 separator, 4 for small firms
        expected_columns = 9
        if len(df.columns) != expected_columns:
            return None, None, (
                f"Unexpected number of columns for default_spread. "
                f"Expected {expected_columns}, got {len(df.columns)}. "
                f"Website schema may have changed."
            )

        # Validate header content
        header_text = ' '.join(str(v) for v in df.iloc[0].values)
        if 'spread' not in header_text.lower():
            return None, None, (
                "Header validation failed for default_spread: "
                "'spread' not found in first row. Website schema may have changed."
            )

        df = df.map(lambda x: x.replace('%', '').strip() if isinstance(x, str) else x)

        # Skip first 4 header rows
        df_data = df.iloc[4:, :]

        # Large firms: columns 0-3, Small firms: columns 5-8 (column 4 is separator)
        large_firms = [tuple(x) for x in df_data.iloc[:, [0, 1, 2, 3]].to_numpy()]
        small_firms = [tuple(x) for x in df_data.iloc[:, [5, 6, 7, 8]].to_numpy()]

        if len(large_firms) < 5:
            return None, None, (
                f"Only {len(large_firms)} rows for default_spread large firms, "
                f"expected at least 5. Data may be incomplete."
            )

        return large_firms, small_firms, None

    except Exception as e:
        logger.error(f"Error in clean_default_spread: {str(e)}", exc_info=True)
        return None, None, str(e)


@retry_on_failure()
def get_last_update(url, text_to_find, date_format="%B %Y"):
    """
    Extract the last update date from a Damodaran page.

    Searches the HTML for text matching text_to_find, then parses
    the date portion using the specified format.

    Args:
        url: Page URL to scrape
        text_to_find: Text pattern to search for (e.g., "Last updated in")
        date_format: strptime format string (default: "%B %Y" for "January 2025",
                     use "%B %d, %Y" for "January 15, 2025")
    """
    logger.info(f"Getting last update from {url}")
    response = fetch_url_with_retry(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    last_updated_text = soup.find(string=lambda t: t and text_to_find in t)
    if last_updated_text:
        last_updated_text = last_updated_text.strip()
        date_part = last_updated_text.replace(f"{text_to_find} ", "")
        date_obj = datetime.strptime(date_part, date_format).date()
        return date_obj

    return None


# NOTE: Excel scraping for fcffsimpleginzu.xlsx has been moved to excel_scraper.py.
# See scrape_xl_input_stats(), scrape_xl_country_equity_risk_premium(), etc.
