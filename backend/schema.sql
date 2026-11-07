-- HIJASI wallet platform schema (MySQL 8 / MariaDB 10.6+)
-- All money is stored as integers in minor units (cents / øre / paise).

CREATE DATABASE IF NOT EXISTS hijasi CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE hijasi;

-- Businesses that integrate with us. Only 'approved' merchants can use the API.
CREATE TABLE IF NOT EXISTS merchants (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  business_name    VARCHAR(150) NOT NULL,
  email            VARCHAR(190) NOT NULL UNIQUE,
  password_hash    VARCHAR(255) NOT NULL,
  website          VARCHAR(255) NOT NULL,
  country          VARCHAR(2)   NOT NULL,           -- ISO 3166 alpha-2
  business_type    VARCHAR(60)  NOT NULL,           -- farm, game_studio, retail, ...
  description      TEXT,
  currency         VARCHAR(3)   NOT NULL DEFAULT 'nok',
  status           ENUM('pending','approved','rejected','suspended') NOT NULL DEFAULT 'pending',
  review_note      VARCHAR(255),
  -- Closed-loop policy limits (minor units). Balances are only spendable at this merchant.
  max_balance      BIGINT NOT NULL DEFAULT 1000000,  -- e.g. 10 000.00
  max_topup        BIGINT NOT NULL DEFAULT 500000,   -- e.g.  5 000.00
  min_topup        BIGINT NOT NULL DEFAULT 1000,     -- e.g.     10.00
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at      TIMESTAMP NULL
) ENGINE=InnoDB;

-- Secret API keys. Only a SHA-256 hash is stored; the raw key is shown once.
CREATE TABLE IF NOT EXISTS api_keys (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  merchant_id  INT NOT NULL,
  key_prefix   VARCHAR(16) NOT NULL,                 -- shown in dashboard, e.g. sk_test_ab12
  key_hash     CHAR(64) NOT NULL UNIQUE,
  revoked      TINYINT(1) NOT NULL DEFAULT 0,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_used_at TIMESTAMP NULL,
  FOREIGN KEY (merchant_id) REFERENCES merchants(id)
) ENGINE=InnoDB;

-- One wallet per (merchant, end-customer). customer_ref is the merchant's own customer id.
CREATE TABLE IF NOT EXISTS wallets (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  merchant_id   INT NOT NULL,
  customer_ref  VARCHAR(100) NOT NULL,
  display_name  VARCHAR(150),
  balance       BIGINT NOT NULL DEFAULT 0,
  status        ENUM('active','frozen') NOT NULL DEFAULT 'active',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_wallet (merchant_id, customer_ref),
  FOREIGN KEY (merchant_id) REFERENCES merchants(id),
  CHECK (balance >= 0)
) ENGINE=InnoDB;

-- Append-only ledger. Every balance change is one row; balance_after makes audits trivial.
CREATE TABLE IF NOT EXISTS ledger_entries (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  public_id        VARCHAR(32) NOT NULL UNIQUE,      -- txn_xxx
  wallet_id        INT NOT NULL,
  merchant_id      INT NOT NULL,
  type             ENUM('topup','charge','refund','adjustment') NOT NULL,
  amount           BIGINT NOT NULL,                  -- signed: + credit, - debit
  balance_after    BIGINT NOT NULL,
  reference        VARCHAR(150),                     -- merchant order id, etc.
  description      VARCHAR(255),
  related_entry_id BIGINT NULL,                      -- refund -> original charge
  idempotency_key  VARCHAR(100) NULL,
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_idem (merchant_id, idempotency_key),
  KEY ix_wallet (wallet_id, id),
  FOREIGN KEY (wallet_id) REFERENCES wallets(id),
  FOREIGN KEY (merchant_id) REFERENCES merchants(id)
) ENGINE=InnoDB;

-- A top-up is pending until the payment gateway confirms it; only then is the wallet credited.
CREATE TABLE IF NOT EXISTS topups (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  public_id          VARCHAR(32) NOT NULL UNIQUE,    -- top_xxx
  wallet_id          INT NOT NULL,
  merchant_id        INT NOT NULL,
  amount             BIGINT NOT NULL,
  currency           VARCHAR(3) NOT NULL,
  provider           ENUM('stripe','mock') NOT NULL,
  provider_ref       VARCHAR(255),                   -- Stripe Checkout Session id
  status             ENUM('pending','succeeded','failed','expired') NOT NULL DEFAULT 'pending',
  return_url         VARCHAR(500),
  ledger_entry_id    BIGINT NULL,
  created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at       TIMESTAMP NULL,
  FOREIGN KEY (wallet_id) REFERENCES wallets(id),
  FOREIGN KEY (merchant_id) REFERENCES merchants(id)
) ENGINE=InnoDB;
