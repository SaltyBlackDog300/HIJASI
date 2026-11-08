"""Kassara plans, fees and monthly usage.

All money is integer minor units (cents). Plan prices are proposed launch prices in EUR.
Everything is configuration: change PLANS (or the env overrides below) without touching the app.

Top-up volume allowances for the subscription plans are NOT decided yet. They are left as None
("not set") and are shown as such in the dashboard and on the pricing page; volume is still tracked.
Set them with env vars once decided, e.g.
    PLAN_BUSINESS_TOPUP_ALLOWANCE=2500000        # 25,000.00 EUR per month
    PLAN_BUSINESS_VOLUME_FEE_BPS=150             # 1.5% on volume above the allowance
If an allowance is set but no volume fee is set, top-ups above the allowance are refused.
"""
import math
import os
from datetime import date

PRICING_CURRENCY = "eur"

PLANS = {
    "starter": {
        "name": "Starter", "monthly_price": 0,
        "topup_fee_bps": 300,            # 3% of the top-up, paid on top by the customer
        "topup_fee_min": 50,             # at least 0.50
        "included_transactions": 500,
        "overage_per_1000": 2000,        # 0.02 per extra transaction
        "topup_volume_allowance": None,  # not applicable: every top-up already pays the 3% fee
        "volume_fee_bps": None,
        "features": ["Full API access", "Business dashboard", "No monthly subscription"],
    },
    "business": {
        "name": "Business", "monthly_price": 2900,
        "topup_fee_bps": 0, "topup_fee_min": 0,
        "included_transactions": 5000,
        "overage_per_1000": 1000,        # 0.01
        "topup_volume_allowance": None,  # TO BE DECIDED
        "volume_fee_bps": None,          # TO BE DECIDED
        "features": ["Full API access", "Business dashboard", "Wallet and customer management",
                     "Transaction history"],
        "highlight": True,
    },
    "business_pro": {
        "name": "Business Pro", "monthly_price": 7900,
        "topup_fee_bps": 0, "topup_fee_min": 0,
        "included_transactions": 25000,
        "overage_per_1000": 500,         # 0.005
        "topup_volume_allowance": None,  # TO BE DECIDED
        "volume_fee_bps": None,          # TO BE DECIDED
        "features": ["Full API access", "Business dashboard", "Advanced wallet management and reporting",
                     "Transaction history"],
    },
}
ORDER = ["starter", "business", "business_pro"]
DEFAULT_PLAN = "starter"


def _env_int(name):
    v = os.getenv(name, "").strip()
    return int(v) if v.isdigit() else None


for _key, _plan in PLANS.items():
    _p = f"PLAN_{_key.upper()}_"
    if _env_int(_p + "TOPUP_ALLOWANCE") is not None:
        _plan["topup_volume_allowance"] = _env_int(_p + "TOPUP_ALLOWANCE")
    if _env_int(_p + "VOLUME_FEE_BPS") is not None:
        _plan["volume_fee_bps"] = _env_int(_p + "VOLUME_FEE_BPS")


def plan(key):
    return PLANS.get(key) or PLANS[DEFAULT_PLAN]


def month_start(today=None):
    t = today or date.today()
    return t.replace(day=1)


def next_month_start(today=None):
    m = month_start(today)
    return m.replace(year=m.year + 1, month=1) if m.month == 12 else m.replace(month=m.month + 1)


def _bps(amount, bps):
    """Percentage fee in basis points, rounded half-up to the cent (integer maths only)."""
    return (amount * bps + 5000) // 10000


def min_fee_for(currency, p):
    """Fixed minimum fees are set in EUR. Other currencies need an explicit setting
    (e.g. KASSARA_MIN_FEE_INR=4500); without one only the percentage applies."""
    if currency.lower() == PRICING_CURRENCY:
        return p["topup_fee_min"]
    return _env_int(f"KASSARA_MIN_FEE_{currency.upper()}") or 0 if p["topup_fee_min"] else 0


def topup_fee(plan_key, currency, amount, volume_this_month):
    """Kassara fee for one top-up. Returns (fee, explanation, blocked_reason)."""
    p = plan(plan_key)
    if p["topup_fee_bps"]:
        fee = max(_bps(amount, p["topup_fee_bps"]), min_fee_for(currency, p))
        minimum = min_fee_for(currency, p)
        label = f"{p['topup_fee_bps'] / 100:g}% Kassara fee" + (
            f" (min. {minimum / 100:.2f} {currency.upper()})" if minimum else "")
        return fee, label, None
    allowance = p["topup_volume_allowance"]
    if allowance is None or volume_this_month + amount <= allowance:
        return 0, "Included in your plan", None
    over = volume_this_month + amount - max(allowance, volume_this_month)
    if p["volume_fee_bps"] is None:
        return 0, None, "monthly top-up volume allowance reached"
    return _bps(over, p["volume_fee_bps"]), f"{p['volume_fee_bps'] / 100:g}% on volume above your allowance", None


def overage_cost(plan_key, transactions):
    p = plan(plan_key)
    extra = max(0, transactions - p["included_transactions"])
    return extra, math.ceil(extra * p["overage_per_1000"] / 1000)


def public_plans():
    out = []
    for k in ORDER:
        p = PLANS[k]
        out.append({
            "id": k, "name": p["name"], "currency": PRICING_CURRENCY,
            "monthly_price": p["monthly_price"],
            "topup_fee_percent": p["topup_fee_bps"] / 100,
            "topup_fee_min": p["topup_fee_min"],
            "included_transactions": p["included_transactions"],
            "overage_per_transaction": p["overage_per_1000"] / 100000,   # in EUR, e.g. 0.005
            "topup_volume_allowance": p["topup_volume_allowance"],
            "volume_fee_percent": None if p["volume_fee_bps"] is None else p["volume_fee_bps"] / 100,
            "features": p["features"], "highlight": bool(p.get("highlight")),
        })
    return out
