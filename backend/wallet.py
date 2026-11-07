"""Closed-loop wallet ledger.

Rules enforced here (the "policy engine"):
  * A wallet belongs to exactly one merchant and can only be spent at that merchant.
  * There is no cash-out / withdrawal operation at all.
  * Balance never goes below zero; every change is an append-only ledger row.
  * Top-ups respect the merchant's min/max top-up and max balance limits.
  * Refunds can never exceed what was charged.
  * Write operations accept an idempotency key so retries never double-charge.
"""
import secrets

from errors import ApiError


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(8)}"


def entry_out(e):
    return {
        "id": e["public_id"],
        "type": e["type"],
        "amount": e["amount"],
        "balance_after": e["balance_after"],
        "reference": e["reference"],
        "description": e["description"],
        "created_at": e["created_at"].isoformat() if e.get("created_at") else None,
    }


def wallet_out(w, currency):
    return {
        "customer_id": w["customer_ref"],
        "display_name": w["display_name"],
        "balance": w["balance"],
        "currency": currency,
        "status": w["status"],
    }


def get_or_create_wallet(cur, merchant_id, customer_ref, display_name=None):
    customer_ref = (customer_ref or "").strip()
    if not customer_ref or len(customer_ref) > 100:
        raise ApiError(400, "invalid_customer", "customer_id must be 1-100 characters")
    cur.execute(
        "INSERT IGNORE INTO wallets (merchant_id, customer_ref, display_name) VALUES (%s, %s, %s)",
        (merchant_id, customer_ref, display_name),
    )
    if display_name:
        cur.execute(
            "UPDATE wallets SET display_name=%s WHERE merchant_id=%s AND customer_ref=%s",
            (display_name, merchant_id, customer_ref),
        )
    cur.execute(
        "SELECT * FROM wallets WHERE merchant_id=%s AND customer_ref=%s",
        (merchant_id, customer_ref),
    )
    return cur.fetchone()


def lock_wallet(cur, merchant_id, customer_ref):
    cur.execute(
        "SELECT * FROM wallets WHERE merchant_id=%s AND customer_ref=%s FOR UPDATE",
        (merchant_id, customer_ref),
    )
    w = cur.fetchone()
    if not w:
        raise ApiError(404, "wallet_not_found", "No wallet for this customer")
    return w


def _existing_by_idem(cur, merchant_id, idem_key):
    if not idem_key:
        return None
    cur.execute(
        "SELECT * FROM ledger_entries WHERE merchant_id=%s AND idempotency_key=%s",
        (merchant_id, idem_key),
    )
    return cur.fetchone()


def _post(cur, wallet, merchant_id, type_, amount, reference=None, description=None,
          related_entry_id=None, idem_key=None):
    """Apply a signed amount to a locked wallet and append the ledger row."""
    new_balance = wallet["balance"] + amount
    if new_balance < 0:
        raise ApiError(402, "insufficient_funds", "Wallet balance is too low",
                       balance=wallet["balance"])
    cur.execute("UPDATE wallets SET balance=%s WHERE id=%s", (new_balance, wallet["id"]))
    public_id = new_id("txn")
    cur.execute(
        """INSERT INTO ledger_entries
           (public_id, wallet_id, merchant_id, type, amount, balance_after, reference,
            description, related_entry_id, idempotency_key)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (public_id, wallet["id"], merchant_id, type_, amount, new_balance, reference,
         description, related_entry_id, idem_key),
    )
    wallet["balance"] = new_balance
    cur.execute("SELECT * FROM ledger_entries WHERE public_id=%s", (public_id,))
    return cur.fetchone()


def _positive_int(value, field="amount"):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ApiError(400, "invalid_amount", f"{field} must be a positive integer in minor units")
    return value


def charge(cur, merchant, customer_ref, amount, reference=None, description=None, idem_key=None):
    amount = _positive_int(amount)
    existing = _existing_by_idem(cur, merchant["id"], idem_key)
    if existing:
        if existing["type"] != "charge" or existing["amount"] != -amount:
            raise ApiError(409, "idempotency_conflict", "Idempotency key reused with different parameters")
        return existing
    wallet = lock_wallet(cur, merchant["id"], customer_ref)
    if wallet["status"] != "active":
        raise ApiError(403, "wallet_frozen", "This wallet is frozen")
    return _post(cur, wallet, merchant["id"], "charge", -amount, reference, description,
                 idem_key=idem_key)


def refund(cur, merchant, charge_public_id, amount=None, reason=None, idem_key=None):
    existing = _existing_by_idem(cur, merchant["id"], idem_key)
    if existing:
        return existing
    cur.execute(
        "SELECT * FROM ledger_entries WHERE public_id=%s AND merchant_id=%s AND type='charge'",
        (charge_public_id, merchant["id"]),
    )
    original = cur.fetchone()
    if not original:
        raise ApiError(404, "charge_not_found", "No charge with that id")
    cur.execute("SELECT * FROM wallets WHERE id=%s FOR UPDATE", (original["wallet_id"],))
    wallet = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM ledger_entries WHERE related_entry_id=%s AND type='refund'",
        (original["id"],),
    )
    already = int(cur.fetchone()["s"])
    refundable = -original["amount"] - already
    amount = refundable if amount is None else _positive_int(amount)
    if amount <= 0 or amount > refundable:
        raise ApiError(400, "refund_exceeds_charge", "Refund is larger than the refundable amount",
                       refundable=refundable)
    return _post(cur, wallet, merchant["id"], "refund", amount, original["reference"],
                 reason or f"Refund of {charge_public_id}", related_entry_id=original["id"],
                 idem_key=idem_key)


def check_topup_allowed(merchant, wallet, amount):
    amount = _positive_int(amount)
    if amount < merchant["min_topup"]:
        raise ApiError(400, "below_min_topup", "Top-up is below the merchant minimum",
                       min_topup=merchant["min_topup"])
    if amount > merchant["max_topup"]:
        raise ApiError(400, "above_max_topup", "Top-up is above the merchant maximum",
                       max_topup=merchant["max_topup"])
    if wallet["balance"] + amount > merchant["max_balance"]:
        raise ApiError(400, "max_balance_exceeded", "Top-up would exceed the maximum wallet balance",
                       max_balance=merchant["max_balance"])
    if wallet["status"] != "active":
        raise ApiError(403, "wallet_frozen", "This wallet is frozen")
    return amount


def credit_topup(cur, topup_id):
    """Credit a confirmed top-up exactly once. Safe to call from webhook and polling."""
    cur.execute("SELECT * FROM topups WHERE id=%s FOR UPDATE", (topup_id,))
    t = cur.fetchone()
    if t["status"] == "succeeded":
        return t
    cur.execute("SELECT * FROM wallets WHERE id=%s FOR UPDATE", (t["wallet_id"],))
    wallet = cur.fetchone()
    entry = _post(cur, wallet, t["merchant_id"], "topup", t["amount"],
                  reference=t["public_id"], description="Wallet top-up")
    cur.execute(
        "UPDATE topups SET status='succeeded', ledger_entry_id=%s, completed_at=NOW() WHERE id=%s",
        (entry["id"], t["id"]),
    )
    cur.execute("SELECT * FROM topups WHERE id=%s", (topup_id,))
    return cur.fetchone()
