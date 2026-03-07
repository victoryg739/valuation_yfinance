from flask import Flask, jsonify
import yfinance as yf
import pandas as pd
from database import DatabaseHandler
from datetime import datetime, date, timedelta
import logging
from functools import wraps
from dotenv import load_dotenv
load_dotenv()

# Configure logging
# Note: Vercel has a read-only file system, so we can't write to files
# Logs will only go to stdout (visible in Vercel logs)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Only use StreamHandler for Vercel
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Whitelist of valid table names to prevent SQL injection in dynamic table operations
ALLOWED_TABLES = frozenset({
    'country_risk_premium', 'effective_tax_rate', 'sales_to_cap_us',
    'beta_us', 'pe_ratio_us', 'rev_growth_rate', 'ebit_growth',
    'default_spread_large_firm', 'default_spread_small_firm', 'roic',
    'input_stats',
})

# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return decorated_function

# Input validation functions
def validate_ticker_symbol(ticker):
    """Validate ticker symbol format"""
    import re
    # Allow alphanumeric characters, dots, hyphens, and carets (for indices like ^TNX)
    if not ticker or not re.match(r'^[\^A-Za-z0-9.-]{1,10}$', ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker}")
    return ticker.upper()

def validate_date_format(date_str):
    """Validate date string in YYYY-MM-DD format"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return date_str
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD")

def validate_currency_code(currency):
    """Validate currency code (3 letters)"""
    import re
    if not currency or not re.match(r'^[A-Z]{3}$', currency.upper()):
        raise ValueError(f"Invalid currency code: {currency}. Expected 3-letter code (e.g., USD)")
    return currency.upper()

def _validate_table_name(table_name):
    """Validate that a table name is in the allowed whitelist."""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Invalid table name: {table_name}")

# Reusable database update function
def update_database_table(
    table_name,
    data_name,
    clean_function,
    last_update_function,
    last_update_url,
    last_update_text,
    insert_query,
    use_time_delta=False,
    delta_days=30
):
    """
    Generic function to update database tables with scraped data.

    Uses atomic transactions: DELETE + INSERT in a single transaction so that
    if the insert fails, the old data is preserved (rolled back).
    """
    logger.info(f"Starting update for {table_name}")

    # Clean and fetch data
    result = clean_function()

    # Handle functions that return multiple data sets (like default_spread)
    is_multi_table = isinstance(result, tuple) and len(result) == 3
    if is_multi_table:
        data_tuple_1, data_tuple_2, error = result
        if error:
            logger.error(f"Error cleaning data for {table_name}: {error}")
            return jsonify({"error": error}), 400
        # Process the data (first 4 elements only for default_spread)
        data_tuple_1 = [(row[0], row[1], row[2], row[3]) for row in data_tuple_1]
        data_tuple_2 = [(row[0], row[1], row[2], row[3]) for row in data_tuple_2]
    else:
        data_tuples, error = result
        if error:
            logger.error(f"Error cleaning data for {table_name}: {error}")
            return jsonify({"error": error}), 400

    # Get last update date
    if use_time_delta:
        last_update = date.today()
    else:
        try:
            last_update = last_update_function(last_update_url, last_update_text)
            if not last_update:
                logger.warning(f"Could not get last update for {table_name}")
                return jsonify({"error": "Could not retrieve last update date"}), 400
        except Exception as e:
            logger.error(f"Error getting last update for {table_name}: {str(e)}")
            return jsonify({"error": f"Error getting last update: {str(e)}"}), 500

    # Database operations
    try:
        with DatabaseHandler() as db_handler:
            # Get database last update
            db_last_update = db_handler.fetch_query(
                "SELECT last_update FROM data_last_update WHERE data_name = %s",
                (data_name,)
            )

            if not db_last_update:
                logger.error(f"Error getting last_update from table data_last_update for {data_name}")
                return jsonify({"status": "Error getting last_update from table data_last_update"}), 500

            db_last_update = db_last_update[0][0]

            # Check if update is needed
            should_update = False
            if use_time_delta:
                should_update = (last_update - db_last_update) > timedelta(days=delta_days)
            else:
                should_update = last_update > db_last_update

            if should_update:
                logger.info(f"Updating {table_name} - new data available")

                # Atomic transaction: DELETE + INSERT + update timestamp
                # If any step fails, everything rolls back and old data is preserved
                if is_multi_table:
                    table_1 = f"{table_name}_large_firm"
                    table_2 = f"{table_name}_small_firm"
                    _validate_table_name(table_1)
                    _validate_table_name(table_2)

                    db_handler.cur.execute(f"DELETE FROM {table_1}")
                    db_handler.cur.execute(f"DELETE FROM {table_2}")
                    db_handler.cur.executemany(insert_query[0], data_tuple_1)
                    db_handler.cur.executemany(insert_query[1], data_tuple_2)
                else:
                    _validate_table_name(table_name)
                    db_handler.cur.execute(f"DELETE FROM {table_name}")
                    db_handler.cur.executemany(insert_query, data_tuples)

                db_handler.cur.execute(
                    "UPDATE data_last_update SET last_update = %s WHERE data_name = %s",
                    (last_update, data_name)
                )
                db_handler.conn.commit()

                logger.info(f"Successfully updated {table_name}")
                return jsonify({"status": "Data inserted successfully"}), 200
            else:
                logger.info(f"No update needed for {table_name} - data is current")
                return jsonify({"status": "Data is the same"}), 200

    except Exception as e:
        logger.error(f"Database error for {table_name}: {str(e)}", exc_info=True)
        return jsonify({"error": f"Database error: {str(e)}"}), 500

def restructure_data(df):
    restructured_data = []

    for column in df.columns:
        date_str = column.strftime('%Y-%m-%d')
        period_data = {"date": date_str, "values": {}}
        for index, value in df[column].items():
            if pd.notna(value):  # Only include non-NaN values
                period_data["values"][index] = float(value)
        restructured_data.append(period_data)

    # Sort the array by date, most recent first
    restructured_data.sort(key=lambda x: x["date"], reverse=True)

    return restructured_data

@app.route('/stock_info/<ticker_symbol>')
@handle_errors
def get_stock_info(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    logger.info(f"Fetching stock info for {ticker_symbol}")
    ticker = yf.Ticker(ticker_symbol)
    return jsonify(ticker.info)

@app.route('/stock_info/tnx')
@handle_errors
def get_stock_info_tnx():
    logger.info("Fetching stock info for ^TNX")
    ticker = yf.Ticker("^TNX")
    return jsonify(ticker.info)

@app.route('/annual_income_statement/<ticker_symbol>')
@handle_errors
def get_annual_income_statement(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    income_statement = ticker.income_stmt
    restructured_data = restructure_data(income_statement)
    return jsonify(restructured_data)

@app.route('/annual_balance_sheet/<ticker_symbol>')
@handle_errors
def get_annual_balance_sheet(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    balance_sheet = ticker.balance_sheet
    restructured_data = restructure_data(balance_sheet)
    return jsonify(restructured_data)

@app.route('/annual_cash_flow/<ticker_symbol>')
@handle_errors
def get_annual_cash_flow(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    cash_flow = ticker.cash_flow
    restructured_data = restructure_data(cash_flow)
    return jsonify(restructured_data)

@app.route('/quarterly_income_statement/<ticker_symbol>')
@handle_errors
def get_quarterly_income_statement(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    income_statement = ticker.quarterly_income_stmt
    restructured_data = restructure_data(income_statement)
    return jsonify(restructured_data)

@app.route('/quarterly_balance_sheet/<ticker_symbol>')
@handle_errors
def get_quarterly_balance_sheet(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    balance_sheet = ticker.quarterly_balance_sheet
    restructured_data = restructure_data(balance_sheet)
    return jsonify(restructured_data)

@app.route('/quarterly_cash_flow/<ticker_symbol>')
@handle_errors
def get_quarterly_cash_flow(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    cash_flow = ticker.quarterly_cash_flow
    restructured_data = restructure_data(cash_flow)
    return jsonify(restructured_data)

@app.route('/ttm_income_statement/<ticker_symbol>')
@handle_errors
def get_ttm_income_statement(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    income_statement = ticker.ttm_income_stmt
    return income_statement.to_json()

@app.route('/ttm_cash_flow/<ticker_symbol>')
@handle_errors
def get_cash_flow(ticker_symbol):
    ticker_symbol = validate_ticker_symbol(ticker_symbol)
    ticker = yf.Ticker(ticker_symbol)
    cash_flow = ticker.ttm_cashflow
    return cash_flow.to_json()

@app.route('/currency_conversion/<source_currency>/<target_currency>/<start_date>/<end_date>')
@handle_errors
def get_currency_conversion(source_currency, target_currency, start_date, end_date):
    # Validate inputs
    source_currency = validate_currency_code(source_currency)
    target_currency = validate_currency_code(target_currency)
    start_date = validate_date_format(start_date)
    end_date = validate_date_format(end_date)

    # Format the currency pair for Yahoo Finance
    currency_pair = f"{source_currency}{target_currency}=X"
    logger.info(f"Fetching currency conversion for {currency_pair} from {start_date} to {end_date}")

    # Get ticker for the currency pair
    ticker = yf.Ticker(currency_pair)

    # Fetch historical data
    historical_data = ticker.history(start=start_date, end=end_date)

    if historical_data.empty:
        return jsonify({"error": "No data found for this currency pair or date range"}), 404

    # Process the data into the same format as other endpoints
    restructured_data = []

    for dt, row in historical_data.iterrows():
        date_str = dt.strftime('%Y-%m-%d')

        # Create an object with date and values
        period_data = {"date": date_str, "values": {}}

        # Add non-NaN values to the values object
        for column, value in row.items():
            if pd.notna(value):  # Only include non-NaN values
                period_data["values"][column] = float(value)

        restructured_data.append(period_data)

    # Sort the array by date, most recent first
    restructured_data.sort(key=lambda x: x["date"], reverse=True)

    return jsonify(restructured_data)

@app.route('/update_country_risk_premium')
@handle_errors
def update_country_risk_premium():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='country_risk_premium',
        data_name='country_risk_premium',
        clean_function=lambda: scrape_table('country_risk_premium'),
        last_update_function=lambda url, text: get_last_update(url, text, date_format="%B %d, %Y"),
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html",
        last_update_text="Last updated:",
        insert_query="INSERT INTO country_risk_premium VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )

@app.route('/update_effective_tax_rate')
@handle_errors
def update_effective_tax_rate():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='effective_tax_rate',
        data_name='effective_tax_rate',
        clean_function=lambda: scrape_table('effective_tax_rate'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/taxrate.html",
        last_update_text="Updated",
        insert_query="INSERT INTO effective_tax_rate VALUES (%s, %s, %s, %s, %s, %s, %s,%s,%s,%s,%s)"
    )

@app.route('/update_sales_to_cap_us')
@handle_errors
def update_sales_to_cap_us():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='sales_to_cap_us',
        data_name='sales_to_cap_us',
        clean_function=lambda: scrape_table('sales_to_cap_us'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html",
        last_update_text="Last updated",
        insert_query="INSERT INTO sales_to_cap_us VALUES (%s, %s, %s, %s, %s, %s, %s,%s,%s,%s)"
    )

@app.route('/update_beta_us')
@handle_errors
def update_beta_us():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='beta_us',
        data_name='beta_us',
        clean_function=lambda: scrape_table('beta_us'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/totalbeta.html",
        last_update_text="Last Updated in",
        insert_query="INSERT INTO beta_us VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )

@app.route('/update_pe_ratio_us')
@handle_errors
def update_pe_ratio_us():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='pe_ratio_us',
        data_name='pe_ratio_us',
        clean_function=lambda: scrape_table('pe_ratio_us'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html",
        last_update_text="Last Updated in",
        insert_query="INSERT INTO pe_ratio_us VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )

@app.route('/update_rev_growth_rate')
@handle_errors
def update_rev_growth_rate():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='rev_growth_rate',
        data_name='rev_growth_rate',
        clean_function=lambda: scrape_table('rev_growth_rate'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histgr.html",
        last_update_text="Last updated in",
        insert_query="INSERT INTO rev_growth_rate VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )

@app.route('/update_ebit_growth')
@handle_errors
def update_ebit_growth():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='ebit_growth',
        data_name='ebit_growth',
        clean_function=lambda: scrape_table('ebit_growth'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/fundgrEB.html",
        last_update_text="Last updated in",
        insert_query="INSERT INTO ebit_growth VALUES (%s, %s, %s, %s, %s)"
    )

@app.route('/update_default_spread')
@handle_errors
def update_default_spread():
    from data_helper import clean_default_spread
    return update_database_table(
        table_name='default_spread',
        data_name='default_spread',
        clean_function=clean_default_spread,
        last_update_function=None,
        last_update_url=None,
        last_update_text=None,
        insert_query=[
            "INSERT INTO default_spread_large_firm VALUES (%s, %s, %s, %s)",
            "INSERT INTO default_spread_small_firm VALUES (%s, %s, %s, %s)"
        ],
        use_time_delta=True,
        delta_days=30
    )

@app.route('/update_roic')
@handle_errors
def update_roic():
    from data_helper import scrape_table, get_last_update
    return update_database_table(
        table_name='roic',
        data_name='roic',
        clean_function=lambda: scrape_table('roic'),
        last_update_function=get_last_update,
        last_update_url="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/fundgrEB.html",
        last_update_text="Last updated in",
        insert_query="INSERT INTO roic VALUES (%s, %s, %s, %s, %s)"
    )

@app.route('/update_all')
@handle_errors
def update_all():
    """Run all update functions and return a summary of results."""
    logger.info("Starting batch update for all data sources")

    update_functions = [
        ('country_risk_premium', update_country_risk_premium),
        ('effective_tax_rate', update_effective_tax_rate),
        ('sales_to_cap_us', update_sales_to_cap_us),
        ('beta_us', update_beta_us),
        ('pe_ratio_us', update_pe_ratio_us),
        ('rev_growth_rate', update_rev_growth_rate),
        ('ebit_growth', update_ebit_growth),
        ('default_spread', update_default_spread),
        ('roic', update_roic),
    ]

    results = {}
    successful = 0
    failed = 0

    for name, func in update_functions:
        try:
            logger.info(f"Updating {name}...")
            response = func()

            # Handle tuple response (response, status_code)
            if isinstance(response, tuple):
                result_data, status_code = response
                result_json = result_data.get_json()
            else:
                result_json = response.get_json()
                status_code = 200

            results[name] = {
                'status': result_json.get('status', 'Unknown'),
                'success': status_code == 200
            }

            if status_code == 200:
                successful += 1
            else:
                failed += 1

        except Exception as e:
            logger.error(f"Error updating {name}: {str(e)}", exc_info=True)
            results[name] = {
                'status': f'Error: {str(e)}',
                'success': False
            }
            failed += 1

    summary = {
        'total': len(update_functions),
        'successful': successful,
        'failed': failed,
        'results': results
    }

    logger.info(f"Batch update complete: {successful} successful, {failed} failed")

    return jsonify(summary), 200 if failed == 0 else 207  # 207 = Multi-Status

@app.route('/init/last-update')
@handle_errors
def initialize_last_update():
    # Define the tables that need tracking
    tables = [
        'beta_us',
        'country_risk_premium',
        'ebit_growth',
        'pe_ratio_us',
        'rev_growth_rate',
        'sales_to_cap_us',
        'effective_tax_rate',
        'default_spread_large_firm',
        'default_spread_small_firm',
        'roic',
        'default_spread'
    ]

    default_date = '2023-01-01'

    with DatabaseHandler() as db_handler:
        # Ensure data_last_update table exists
        db_handler.execute_query("""
            CREATE TABLE IF NOT EXISTS data_last_update (
                data_name varchar(255),
                last_update date,
                PRIMARY KEY (data_name)
            )
        """)

        for table_name in tables:
            # Use UPSERT to simplify logic
            db_handler.execute_query(
                """INSERT INTO data_last_update (data_name, last_update) VALUES (%s, %s)
                   ON CONFLICT (data_name) DO UPDATE SET last_update = EXCLUDED.last_update""",
                (table_name, default_date)
            )

    return jsonify({
        'status': 'success',
        'message': f'Successfully initialized last_update dates to {default_date} for all tables'
    }), 200

# Ensure Flask app runs
if __name__ == '__main__':
    app.run(debug=True)
