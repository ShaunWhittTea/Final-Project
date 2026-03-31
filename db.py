import psycopg
import os

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn
