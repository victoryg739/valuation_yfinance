#!/usr/bin/env python3
"""
Script to run all data scraping and update routes in one command.
This script sends requests to all update endpoints and reports the results.
"""

import requests
import json
import sys
import logging
from datetime import datetime
from typing import Dict, List, Tuple
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'update_run_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://localhost:5000"  # Change this to your server URL
REQUEST_TIMEOUT = 300  # 5 minutes timeout for each request

# List of all update endpoints
UPDATE_ENDPOINTS = [
    '/update_country_risk_premium',
    '/update_effective_tax_rate',
    '/update_sales_to_cap_us',
    '/update_beta_us',
    '/update_pe_ratio_us',
    '/update_rev_growth_rate',
    '/update_ebit_growth',
    '/update_default_spread',
    '/update_roic',
    '/update_input_stats',
]

def run_update(endpoint: str, base_url: str = BASE_URL, timeout: int = REQUEST_TIMEOUT) -> Tuple[bool, str, Dict]:
    """
    Run a single update endpoint and return the result.

    Args:
        endpoint: The endpoint to call (e.g., '/update_country_risk_premium')
        base_url: Base URL of the Flask server
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success, message, response_data)
    """
    url = f"{base_url}{endpoint}"
    logger.info(f"Starting update for: {endpoint}")

    try:
        response = requests.get(url, timeout=timeout)

        if response.status_code == 200:
            data = response.json()
            status = data.get('status', 'Unknown')

            if 'inserted successfully' in status:
                logger.info(f"✓ {endpoint}: Data updated successfully")
                return True, status, data
            elif 'same' in status:
                logger.info(f"○ {endpoint}: Data is already up to date")
                return True, status, data
            else:
                logger.info(f"✓ {endpoint}: {status}")
                return True, status, data
        else:
            error_msg = response.json().get('error', 'Unknown error') if response.text else 'Unknown error'
            logger.error(f"✗ {endpoint}: Failed with status {response.status_code} - {error_msg}")
            return False, f"HTTP {response.status_code}: {error_msg}", {}

    except requests.exceptions.Timeout:
        error_msg = f"Request timeout after {timeout} seconds"
        logger.error(f"✗ {endpoint}: {error_msg}")
        return False, error_msg, {}
    except requests.exceptions.RequestException as e:
        error_msg = f"Request failed: {str(e)}"
        logger.error(f"✗ {endpoint}: {error_msg}")
        return False, error_msg, {}
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"✗ {endpoint}: {error_msg}")
        return False, error_msg, {}

def run_all_updates(base_url: str = BASE_URL, parallel: bool = False) -> Dict[str, Tuple[bool, str, Dict]]:
    """
    Run all update endpoints.

    Args:
        base_url: Base URL of the Flask server
        parallel: If True, run updates in parallel (not implemented yet, sequential for safety)

    Returns:
        Dictionary mapping endpoint names to their results
    """
    logger.info("=" * 80)
    logger.info("Starting all data updates")
    logger.info("=" * 80)

    start_time = time.time()
    results = {}

    for endpoint in UPDATE_ENDPOINTS:
        success, message, data = run_update(endpoint, base_url)
        results[endpoint] = (success, message, data)

        # Small delay between requests to avoid overwhelming the server
        time.sleep(1)

    elapsed_time = time.time() - start_time

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Update Summary")
    logger.info("=" * 80)

    successful = sum(1 for success, _, _ in results.values() if success)
    failed = len(results) - successful

    logger.info(f"Total endpoints: {len(results)}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Total time: {elapsed_time:.2f} seconds")

    if failed > 0:
        logger.info("\nFailed endpoints:")
        for endpoint, (success, message, _) in results.items():
            if not success:
                logger.info(f"  {endpoint}: {message}")

    logger.info("=" * 80)

    return results

def main():
    """
    Main entry point for the script.
    """
    # Allow custom base URL from command line
    base_url = BASE_URL
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
        logger.info(f"Using custom base URL: {base_url}")

    # Check if server is running
    try:
        logger.info(f"Checking if server is running at {base_url}...")
        response = requests.get(base_url, timeout=5)
        logger.info("✓ Server is running")
    except requests.exceptions.RequestException as e:
        logger.error(f"✗ Cannot connect to server at {base_url}")
        logger.error(f"Error: {str(e)}")
        logger.error("Please make sure the Flask server is running before executing this script.")
        sys.exit(1)

    # Run all updates
    results = run_all_updates(base_url)

    # Exit with appropriate code
    failed_count = sum(1 for success, _, _ in results.values() if not success)
    sys.exit(0 if failed_count == 0 else 1)

if __name__ == "__main__":
    main()
