import os
from pathlib import Path
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def init_db():
    schema_path = Path(__file__).with_name("schema.sql")

    conn = get_db()
    try:
        with conn.cursor() as cur, open(schema_path, "r", encoding="utf-8") as f:
            cur.execute(f.read())
    finally:
        conn.close()
