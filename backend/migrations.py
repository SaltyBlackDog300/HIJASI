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
    # pricing
    ("merchants", "plan", "VARCHAR(20) NOT NULL DEFAULT 'starter'", None),
    ("merchants", "plan_pending", "VARCHAR(20) NULL", None),          # scheduled downgrade
    ("merchants", "plan_pending_from", "DATE NULL", None),
    ("topups", "fee", "BIGINT NOT NULL DEFAULT 0", None),              # Kassara fee, paid on top
    ("topups", "total_charged", "BIGINT NULL", None),                  # amount + fee
    ("topups", "plan", "VARCHAR(20) NULL", None),
]

TABLES = [
    """CREATE TABLE IF NOT EXISTS plan_changes (
      id           INT AUTO_INCREMENT PRIMARY KEY,
      merchant_id  INT NOT NULL,
      from_plan    VARCHAR(20) NOT NULL,
      to_plan      VARCHAR(20) NOT NULL,
      effective_on DATE NOT NULL,
      kind         ENUM('upgrade','downgrade','cancel_downgrade') NOT NULL,
      created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY ix_merchant (merchant_id, id)
    ) ENGINE=InnoDB""",
]


def run():
    for ddl in TABLES:
        query(ddl)
    existing = {(r["TABLE_NAME"], r["COLUMN_NAME"]) for r in query(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")}
    for table, column, definition, after in COLUMNS:
        if (table, column) not in existing:
            query(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
            if after:
                query(after)
