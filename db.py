import os
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    with open("schema.sql", "r", encoding="utf-8") as f:
        cur.execute(f.read())

    cur.close()
    conn.close()
