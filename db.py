import os
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "4"))
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))

_pool = None


def _require_database_url():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")


def get_pool():
    global _pool
    _require_database_url()

    if _pool is None:
        _pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": CONNECT_TIMEOUT,
            },
            open=True,
        )

    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def init_db():
    with get_conn() as conn:
        with open("schema.sql", "r", encoding="utf-8") as f:
            conn.execute(f.read())
        conn.commit()


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
