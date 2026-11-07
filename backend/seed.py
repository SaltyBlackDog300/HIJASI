"""Create the database tables and an approved demo merchant (Green Valley Farm).

Usage:  python seed.py [currency]      e.g. python seed.py eur
Writes the demo merchant's secret key into ../demo-farm/.env.
"""
import os
import secrets
import sys

import pymysql
from werkzeug.security import generate_password_hash

import config
from app import hash_key

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_EMAIL = "farm@demo.hijasi"
DEMO_PASSWORD = "farmdemo123"


def run_schema():
    conn = pymysql.connect(host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
                           password=config.DB_PASSWORD, autocommit=True)
    sql = open(os.path.join(HERE, "schema.sql"), encoding="utf-8").read()
    sql = sql.replace("CREATE DATABASE IF NOT EXISTS hijasi", f"CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}`")
    sql = sql.replace("USE hijasi;", f"USE `{config.DB_NAME}`;")
    sql = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())  # drop SQL comments
    with conn.cursor() as cur:
        for stmt in (s.strip() for s in sql.split(";")):
            if stmt:
                cur.execute(stmt)
    conn.close()


def seed_demo_merchant(currency):
    from db import tx
    with tx() as cur:
        cur.execute("SELECT id FROM merchants WHERE email=%s", (DEMO_EMAIL,))
        row = cur.fetchone()
        if row:
            mid = row["id"]
        else:
            cur.execute(
                """INSERT INTO merchants (business_name, email, password_hash, website, country, business_type,
                   description, currency, status, reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'approved',NOW())""",
                ("Green Valley Farm", DEMO_EMAIL, generate_password_hash(DEMO_PASSWORD),
                 "http://localhost:5001", "IN", "farm",
                 "Family farm delivering weekly vegetable boxes, eggs and milk. Customers prepay monthly.",
                 currency),
            )
            mid = cur.lastrowid
        raw = "sk_test_" + secrets.token_urlsafe(24)
        cur.execute("UPDATE api_keys SET revoked=1 WHERE merchant_id=%s", (mid,))
        cur.execute("INSERT INTO api_keys (merchant_id, key_prefix, key_hash) VALUES (%s,%s,%s)",
                    (mid, raw[:12], hash_key(raw)))

    env_path = os.path.join(HERE, "..", "demo-farm", ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"HIJASI_URL={config.PUBLIC_URL}\nHIJASI_SECRET_KEY={raw}\n"
                f"FARM_URL=http://localhost:5001\nFARM_SECRET_KEY={secrets.token_hex(16)}\n")
    return raw


if __name__ == "__main__":
    currency = (sys.argv[1] if len(sys.argv) > 1 else "eur").lower()
    run_schema()
    print("Schema ready.")
    key = seed_demo_merchant(currency)
    print(f"Demo merchant: {DEMO_EMAIL} / {DEMO_PASSWORD}  (currency {currency})")
    print(f"Secret key written to demo-farm/.env  ({key[:12]}…)")
