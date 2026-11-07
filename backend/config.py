import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "hijasi")

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-change-me")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-dev-token")

# Public base URL of this server (used for mock-payment redirect pages).
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://127.0.0.1:5000").rstrip("/")

# Stripe test mode. Leave empty to use the built-in mock gateway.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Wallet checkout: show the one-time code on the page instead of sending an SMS (demo only).
DEMO_SHOW_CODES = os.getenv("DEMO_SHOW_CODES", "true").lower() in ("1", "true", "yes")

# Production (https://kassara.org): secure cookies, HSTS, and trust the host's HTTPS proxy headers.
PRODUCTION = os.getenv("PRODUCTION", "false").lower() in ("1", "true", "yes")
TRUST_PROXY = os.getenv("TRUST_PROXY", "true" if PRODUCTION else "false").lower() in ("1", "true", "yes")

# Where the "demo shop" links on the landing page go.
DEMO_SHOP_URL = os.getenv("DEMO_SHOP_URL", "http://127.0.0.1:5001").rstrip("/")

# Outgoing email (cPanel mailbox). Leave SMTP_HOST empty to print emails to the log instead.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "no-reply@kassara.org")
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "Kassara")
