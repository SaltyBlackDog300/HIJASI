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
