"""Idempotent schema upgrades for databases created before a feature existed (e.g. the live server)."""
from db import query

COLUMNS = [
    # table, column, definition, run after adding (once)
    ("merchants", "email_verified", "TINYINT(1) NOT NULL DEFAULT 0",
     "UPDATE merchants SET email_verified=1"),          # accounts that existed before verification
    ("merchants", "verify_token_hash", "CHAR(64) NULL", None),
    ("merchants", "verify_expires_at", "DATETIME NULL", None),
    ("merchants", "verify_sent_at", "DATETIME NULL", None),
    ("wallets", "email", "VARCHAR(190) NULL", None),
]


def run():
    existing = {(r["TABLE_NAME"], r["COLUMN_NAME"]) for r in query(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")}
    for table, column, definition, after in COLUMNS:
        if (table, column) not in existing:
            query(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
            if after:
                query(after)
