"""Entry point for cPanel "Setup Python App" (Passenger). Do not rename."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app as application  # noqa: E402

# Create the wallet-checkout and verification tables if they are missing (safe to repeat).
try:
    import checkout
    from app import VERIFICATION_ENGINE
    from merchant_verification.models import Base as _VerificationBase

    _VerificationBase.metadata.create_all(VERIFICATION_ENGINE)
    checkout.ensure_tables()
    import migrations
    migrations.run()
except Exception as exc:  # the app still starts; run seed.py to create the schema
    print(f"[kassara] table check skipped: {exc}", file=sys.stderr)
