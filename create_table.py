from database import DatabaseHandler


db_handler = DatabaseHandler()
 # Connect to the database
db_handler.connect()

data_last_update_sql = """
CREATE TABLE data_last_update (
    data_name varchar(255),
    last_update date ,
    PRIMARY KEY (data_name)
) """

effective_tax_rate_sql = """
CREATE TABLE effective_tax_rate (
    industry varchar(255),
    no_of_firms varchar(255),
    total_taxable_income varchar(255),
    total_taxes_paid_accrual varchar(255),
    total_cash_taxes_paid varchar(255),
    cash_taxes_accrual_taxes varchar(255),
     effectivetr_avg_across_all_comp varchar(255),
    effectivetr_avg_across_money_making_comp varchar(255),
    effectivetr_agg_tax_rate varchar(255),
    cashtr_avg_across_money_making_comp varchar(255),
    cashtr_agg_tax_rate varchar(255),
    PRIMARY KEY (industry)
) """

sales_to_cap_us_sql = """
CREATE TABLE sales_to_cap_us (
    industry varchar(255),
    no_of_firms varchar(255),
    capex varchar(255),
    depre_amort varchar(255),
    capex_depre varchar(255),
    acquisitions varchar(255),
    net_r_and_d varchar(255),
    net_capex_sales varchar(255),
    net_capex_ebit_aftertax varchar(255),
    sales_invested_capital varchar(255),
    PRIMARY KEY (industry)
) """

beta_us_sql = """
CREATE TABLE beta_us (
    industry varchar(255),
    no_of_firms varchar(255),
    avg_unlevered_beta varchar(255),
    avg_levered_beta varchar(255),
    avg_correlation_with_mkt varchar(255),
    total_unlevered_beta varchar(255),
    total_levered_beta varchar(255),
    PRIMARY KEY (industry)
) """

pe_ratio_us_sql = """CREATE TABLE pe_ratio_us (
    industry varchar(255),
    no_of_firms varchar(255),
    perc_money_losing_firms_trailing varchar(255),
    current_pe varchar(255),
    trailing_pe varchar(255),
    forward_pe varchar(255),
    agg_mkt_cap_net_income varchar(255),
    agg_mkt_cap_trailing_net_income_money_making_firms varchar(255),
    expected_growth_next_5_yrs varchar(255),
    peg_ratio varchar(255),
    PRIMARY KEY (industry)
    )"""

rev_growth_rate_sql = """CREATE TABLE rev_growth_rate (
    industry varchar(255),
    no_of_firms varchar(255),
    cagr_net_income_last_5_years varchar(255),
    cagr_net_rev_last_5_years varchar(255),
    expected_growth_rev_next_2_years varchar(255),
    expected_growth_rev_next_5_years varchar(255),
    expected_growth_eps_next_5_years varchar(255),
    PRIMARY KEY (industry)
    )"""

ebit_growth_sql = """CREATE TABLE ebit_growth (
    industry varchar(255),
    no_of_firms varchar(255),
    roc varchar(255),
    reinvestment_rate varchar(255),
    expected_growth_ebit varchar(255),
    PRIMARY KEY (industry)
    )"""

default_spread_large_firm_sql = """CREATE TABLE default_spread_large_firm (
    min varchar(255),
    max varchar(255),
    rating varchar(255),
    spread varchar(255),
    PRIMARY KEY (rating)
    )"""

default_spread_small_firm_sql = """CREATE TABLE default_spread_small_firm (
    min varchar(255),
    max varchar(255),
    rating varchar(255),
    spread varchar(255),
    PRIMARY KEY (rating)
    )"""

# DEPRECATED: input_stats is superseded by xl_input_stats (scraped via excel_scraper.py)
input_stats_sql = """
CREATE TABLE input_stats(
 industry TEXT PRIMARY KEY,
    count INTEGER,
    revenue_growth_rate_first_quartile REAL,
    revenue_growth_rate_median REAL,
    revenue_growth_rate_third_quartile REAL,
    pre_tax_operating_margin_first_quartile REAL,
    pre_tax_operating_margin_median REAL,
    pre_tax_operating_margin_third_quartile REAL,
    sales_to_invested_capital_first_quartile REAL,
    sales_to_invested_capital_median REAL,
    sales_to_invested_capital_third_quartile REAL,
    cost_of_capital_first_quartile REAL,
    cost_of_capital_median REAL,
    cost_of_capital_third_quartile REAL,
    beta_first_quartile REAL,
    beta_median REAL,
    beta_third_quartile REAL,
    debt_to_capital_ratio_first_quartile REAL,
    debt_to_capital_ratio_median REAL,
    debt_to_capital_ratio_third_quartile REAL
)
"""


# ── Excel-sourced tables (all prefixed xl_) ───────────────────────────────────
# Populated by excel_scraper.py — run standalone: python excel_scraper.py [--write-db]

xl_country_equity_risk_premium_sql = """
CREATE TABLE xl_country_equity_risk_premium (
    country TEXT PRIMARY KEY,
    moody_rating TEXT,
    adj_default_spread REAL,
    equity_risk_premium REAL,
    country_risk_premium REAL,
    corporate_tax_rate REAL,
    mature_market_erp REAL
)"""

xl_industry_averages_us_sql = """
CREATE TABLE xl_industry_averages_us (
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
    pretax_operating_margin_adj REAL
)"""

xl_industry_averages_global_sql = """
CREATE TABLE xl_industry_averages_global (
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
    pretax_operating_margin_adj REAL
)"""

xl_input_stats_sql = """
CREATE TABLE xl_input_stats (
    industry TEXT PRIMARY KEY,
    count INTEGER,
    revenue_growth_rate_first_quartile REAL,
    revenue_growth_rate_median REAL,
    revenue_growth_rate_third_quartile REAL,
    pre_tax_operating_margin_first_quartile REAL,
    pre_tax_operating_margin_median REAL,
    pre_tax_operating_margin_third_quartile REAL,
    sales_to_invested_capital_first_quartile REAL,
    sales_to_invested_capital_median REAL,
    sales_to_invested_capital_third_quartile REAL,
    cost_of_capital_first_quartile REAL,
    cost_of_capital_median REAL,
    cost_of_capital_third_quartile REAL,
    beta_first_quartile REAL,
    beta_median REAL,
    beta_third_quartile REAL,
    debt_to_capital_ratio_first_quartile REAL,
    debt_to_capital_ratio_median REAL,
    debt_to_capital_ratio_third_quartile REAL
)"""

xl_rd_amortizable_lives_sql = """
CREATE TABLE xl_rd_amortizable_lives (
    business_type TEXT PRIMARY KEY,
    amortizable_life_years REAL
)"""

xl_synthetic_rating_large_firm_sql = """
CREATE TABLE xl_synthetic_rating_large_firm (
    min_coverage TEXT,
    max_coverage TEXT,
    rating TEXT PRIMARY KEY,
    spread TEXT
)"""

xl_synthetic_rating_small_firm_sql = """
CREATE TABLE xl_synthetic_rating_small_firm (
    min_coverage TEXT,
    max_coverage TEXT,
    rating TEXT PRIMARY KEY,
    spread TEXT
)"""

xl_cost_of_capital_histogram_sql = """
CREATE TABLE xl_cost_of_capital_histogram (
    region TEXT PRIMARY KEY,
    first_decile REAL,
    first_quartile REAL,
    median REAL,
    third_quartile REAL,
    ninth_decile REAL
)"""


valuation_sql = """
CREATE TABLE valuation (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(50),                -- Symbol of the stock
    email VARCHAR(50),                 -- Email associated with the valuation
    inputs JSONB,                      -- JSON data containing input values
    fetched_inputs JSONB,              -- JSON data containing fetched inputs
    stock_info JSONB,                  -- JSON data containing stock information
    valuation_model JSONB,             -- JSON data containing valuation model
    valuation_output JSONB,            -- JSON data containing valuation output
    implied_share_price DECIMAL(10, 2),-- Implied share price of the stock
    roic_data  JSONB,
    description TEXT,                  -- Description of the valuation
    valued_date INTEGER                -- EpochTime
)
"""

roic_sql = """CREATE TABLE roic (
    industry varchar(255),
    no_of_firms varchar(255),
    roc varchar(255),
    reinvestment_rate varchar(255),
    expected_growth_ebit varchar(255),
    PRIMARY KEY (industry)
    )"""

country_risk__premium_sql = """CREATE TABLE country_risk_premium (
    country varchar(255),
    moody_rating varchar(255),
    adj_default_spread varchar(255),
    country_risk_premium varchar(255),
    equity_risk_premium varchar(255),
    corporate_tax_rate varchar(255),
    sovereign_cds varchar(255),
    erp_based_on_sovereign_cds varchar(255),
    PRIMARY KEY (country)
)"""




db_handler.execute_query(valuation_sql)