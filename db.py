import os
from psycopg import connect
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    return connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        with open("schema.sql", "r", encoding="utf-8") as f:
            conn.execute(f.read())
        conn.commit()