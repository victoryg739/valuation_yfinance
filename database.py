import os
import logging
import psycopg2

logger = logging.getLogger(__name__)


class DatabaseHandler:
    def __init__(self):
        self.db_params = {
            'dbname': os.environ.get('DB_NAME', 'verceldb'),
            'user': os.environ.get('DB_USER', 'default'),
            'password': os.environ['DB_PASSWORD'],
            'host': os.environ.get('DB_HOST', 'ep-noisy-waterfall-a1jfpfg9.ap-southeast-1.aws.neon.tech'),
            'port': os.environ.get('DB_PORT', '5432'),
        }
        self.conn = None
        self.cur = None

    def connect(self):
        """Establish a connection to the PostgreSQL database."""
        self.conn = psycopg2.connect(**self.db_params)
        self.cur = self.conn.cursor()
        logger.info("Database connection established.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        self.close()
        return False

    def execute_query(self, query, params=None):
        """Execute a query and commit the transaction."""
        try:
            self.cur.execute(query, params)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Query failed: {e}")
            self.conn.rollback()
            raise

    def execute_query_many(self, query, data):
        """Execute a query with multiple sets of parameters."""
        try:
            self.cur.executemany(query, data)
            self.conn.commit()
        except Exception as e:
            logger.error(f"Batch query failed: {e}")
            self.conn.rollback()
            raise

    def fetch_query(self, query, params=None):
        """Execute a SELECT query and return the results."""
        self.cur.execute(query, params)
        return self.cur.fetchall()

    def rollback(self):
        """Rollback the current transaction."""
        try:
            if self.conn:
                self.conn.rollback()
        except Exception as e:
            logger.error(f"Rollback failed: {e}")

    def close(self):
        """Close the cursor and connection."""
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()
        logger.info("Database connection closed.")
