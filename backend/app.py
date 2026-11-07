import hashlib
import os
import re
import secrets
from functools import wraps
from urllib.parse import urlencode, urlparse

from flask import Flask, g, jsonify, redirect, render_template_string, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import config
import payments
import wallet as W
from db import query, tx
from errors import ApiError

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.secret_key = config.SECRET_KEY
app.config.update(SESSION_COOKIE_NAME="hijasi_session", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax")

BUSINESS_TYPES = {"farm", "grocery", "restaurant", "game_studio", "retail", "education", "services", "other"}
RISK_WORDS = ["crypto", "bitcoin", "token sale", "nft", "forex", "casino", "gambling", "betting",
              "loan", "remittance", "money transfer"]


# ---------------------------------------------------------------- helpers

@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify(e.to_dict()), e.status


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object")
    return data


def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def valid_http_url(url):
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def merchant_public(m):
    return {
        "id": m["id"],
        "business_name": m["business_name"],
        "email": m["email"],
        "website": m["website"],
        "country": m["country"],
        "business_type": m["business_type"],
        "description": m["description"],
        "currency": m["currency"],
        "status": m["status"],
        "review_note": m["review_note"],
        "policy": {"min_topup": m["min_topup"], "max_topup": m["max_topup"],
                   "max_balance": m["max_balance"]},
        "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
    }


def risk_flags(m):
    text = f"{m['business_name']} {m['website']} {m['description'] or ''}".lower()
    return [w for w in RISK_WORDS if w in text]


# ---------------------------------------------------------------- auth decorators

def api_key_required(fn):
    """Server-to-server auth for merchants: Authorization: Bearer sk_test_..."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError(401, "missing_api_key", "Send Authorization: Bearer <secret key>")
        row = query(
            """SELECT m.*, k.id AS key_id FROM api_keys k JOIN merchants m ON m.id = k.merchant_id
               WHERE k.key_hash=%s AND k.revoked=0""",
            (hash_key(auth[7:].strip()),), one=True,
        )
        if not row:
            raise ApiError(401, "invalid_api_key", "API key is invalid or revoked")
        if row["status"] != "approved":
            raise ApiError(403, "merchant_not_approved", "Merchant account is not approved")
        query("UPDATE api_keys SET last_used_at=NOW() WHERE id=%s", (row["key_id"],))
        g.merchant = row
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        mid = session.get("merchant_id")
        m = query("SELECT * FROM merchants WHERE id=%s", (mid,), one=True) if mid else None
        if not m:
            raise ApiError(401, "not_logged_in", "Please log in")
        g.merchant = m
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            raise ApiError(401, "admin_only", "Admin login required")
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- pages

@app.get("/")
def page_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/merchant")
def page_merchant():
    return send_from_directory(FRONTEND_DIR, "merchant.html")


@app.get("/admin")
def page_admin():
    return send_from_directory(FRONTEND_DIR, "admin.html")


@app.get("/docs")
def page_docs():
    return send_from_directory(FRONTEND_DIR, "docs.html")


# ---------------------------------------------------------------- merchant dashboard API

@app.post("/api/auth/register")
def register():
    d = body()
    name = (d.get("business_name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    website = (d.get("website") or "").strip()
    country = (d.get("country") or "").strip().upper()
    btype = (d.get("business_type") or "").strip()
    currency = (d.get("currency") or "nok").strip().lower()
    description = (d.get("description") or "").strip()[:2000]

    if not name or len(name) > 150:
        raise ApiError(400, "invalid_name", "Business name is required")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(400, "invalid_email", "Enter a valid email")
    if len(password) < 8:
        raise ApiError(400, "weak_password", "Password must be at least 8 characters")
    if not valid_http_url(website):
        raise ApiError(400, "invalid_website", "Website must be a full http(s) URL")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ApiError(400, "invalid_country", "Country must be a 2-letter code, e.g. IN")
    if btype not in BUSINESS_TYPES:
        raise ApiError(400, "invalid_business_type", "Unknown business type")
    if not re.fullmatch(r"[a-z]{3}", currency):
        raise ApiError(400, "invalid_currency", "Currency must be a 3-letter code, e.g. inr")

    if query("SELECT id FROM merchants WHERE email=%s", (email,), one=True):
        raise ApiError(409, "email_taken", "An account with this email already exists")
    with tx() as cur:
        cur.execute(
            """INSERT INTO merchants (business_name, email, password_hash, website, country,
               business_type, description, currency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, email, generate_password_hash(password), website, country, btype,
             description, currency),
        )
        mid = cur.lastrowid
    session["merchant_id"] = mid
    return jsonify({"ok": True}), 201


@app.post("/api/auth/login")
def login():
    d = body()
    m = query("SELECT * FROM merchants WHERE email=%s",
              ((d.get("email") or "").strip().lower(),), one=True)
    if not m or not check_password_hash(m["password_hash"], d.get("password") or ""):
        raise ApiError(401, "bad_credentials", "Wrong email or password")
    session["merchant_id"] = m["id"]
    return jsonify({"ok": True})


@app.post("/api/auth/logout")
def logout():
    session.pop("merchant_id", None)
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required
def me():
    m = g.merchant
    keys = query("SELECT key_prefix, created_at, last_used_at FROM api_keys "
                 "WHERE merchant_id=%s AND revoked=0", (m["id"],))
    return jsonify({
        "merchant": merchant_public(m),
        "api_keys": [{"prefix": k["key_prefix"],
                      "created_at": k["created_at"].isoformat(),
                      "last_used_at": k["last_used_at"].isoformat() if k["last_used_at"] else None}
                     for k in keys],
        "gateway": payments.provider_name(),
    })


@app.post("/api/keys")
@login_required
def roll_key():
    m = g.merchant
    if m["status"] != "approved":
        raise ApiError(403, "merchant_not_approved", "Your account must be approved first")
    raw = "sk_test_" + secrets.token_urlsafe(24)
    with tx() as cur:
        cur.execute("UPDATE api_keys SET revoked=1 WHERE merchant_id=%s", (m["id"],))
        cur.execute("INSERT INTO api_keys (merchant_id, key_prefix, key_hash) VALUES (%s,%s,%s)",
                    (m["id"], raw[:12], hash_key(raw)))
    return jsonify({"secret_key": raw, "note": "Copy this now. It will not be shown again."}), 201


@app.put("/api/policy")
@login_required
def update_policy():
    d = body()
    vals = {}
    for f in ("min_topup", "max_topup", "max_balance"):
        v = d.get(f)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ApiError(400, "invalid_policy", f"{f} must be a positive integer (minor units)")
        vals[f] = v
    if not vals["min_topup"] <= vals["max_topup"] <= vals["max_balance"]:
        raise ApiError(400, "invalid_policy", "Need min_topup <= max_topup <= max_balance")
    query("UPDATE merchants SET min_topup=%s, max_topup=%s, max_balance=%s WHERE id=%s",
          (vals["min_topup"], vals["max_topup"], vals["max_balance"], g.merchant["id"]))
    return jsonify({"ok": True, "policy": vals})


@app.get("/api/stats")
@login_required
def merchant_stats():
    mid = g.merchant["id"]
    w = query("SELECT COUNT(*) AS wallets, COALESCE(SUM(balance),0) AS float_total "
              "FROM wallets WHERE merchant_id=%s", (mid,), one=True)
    sums = query("SELECT type, COUNT(*) AS n, COALESCE(SUM(amount),0) AS total "
                 "FROM ledger_entries WHERE merchant_id=%s GROUP BY type", (mid,))
    by = {s["type"]: {"count": s["n"], "total": int(s["total"])} for s in sums}
    return jsonify({"wallets": w["wallets"], "float_total": int(w["float_total"]), "by_type": by,
                    "currency": g.merchant["currency"]})


@app.get("/api/wallets")
@login_required
def merchant_wallets():
    rows = query("SELECT * FROM wallets WHERE merchant_id=%s ORDER BY id DESC LIMIT 200",
                 (g.merchant["id"],))
    return jsonify({"wallets": [W.wallet_out(r, g.merchant["currency"]) for r in rows]})


@app.get("/api/transactions")
@login_required
def merchant_transactions():
    rows = query(
        """SELECT e.*, w.customer_ref FROM ledger_entries e JOIN wallets w ON w.id=e.wallet_id
           WHERE e.merchant_id=%s ORDER BY e.id DESC LIMIT 100""", (g.merchant["id"],))
    return jsonify({"transactions": [{**W.entry_out(r), "customer_id": r["customer_ref"]}
                                     for r in rows]})


# ---------------------------------------------------------------- admin API

@app.post("/api/admin/login")
def admin_login():
    d = body()
    if not secrets.compare_digest(str(d.get("token") or ""), config.ADMIN_TOKEN):
        raise ApiError(401, "bad_token", "Wrong admin token")
    session["is_admin"] = True
    return jsonify({"ok": True})


@app.get("/api/admin/merchants")
@admin_required
def admin_merchants():
    rows = query("SELECT * FROM merchants ORDER BY FIELD(status,'pending','approved','suspended',"
                 "'rejected'), created_at DESC")
    return jsonify({"merchants": [{**merchant_public(r), "risk_flags": risk_flags(r)} for r in rows]})


@app.post("/api/admin/merchants/<int:mid>/review")
@admin_required
def admin_review(mid):
    d = body()
    status = {"approve": "approved", "reject": "rejected", "suspend": "suspended"}.get(d.get("decision"))
    if not status:
        raise ApiError(400, "invalid_decision", "decision must be approve, reject or suspend")
    note = (d.get("note") or "").strip()[:255] or None
    with tx() as cur:
        cur.execute("UPDATE merchants SET status=%s, review_note=%s, reviewed_at=NOW() WHERE id=%s",
                    (status, note, mid))
        if cur.rowcount == 0:
            raise ApiError(404, "not_found", "Merchant not found")
        if status != "approved":
            cur.execute("UPDATE api_keys SET revoked=1 WHERE merchant_id=%s", (mid,))
    return jsonify({"ok": True, "status": status})


@app.get("/api/admin/stats")
@admin_required
def admin_stats():
    m = query("SELECT status, COUNT(*) AS n FROM merchants GROUP BY status")
    f = query("SELECT COALESCE(SUM(balance),0) AS s, COUNT(*) AS n FROM wallets", one=True)
    v = query("SELECT COALESCE(SUM(amount),0) AS s FROM ledger_entries WHERE type='topup'", one=True)
    return jsonify({"merchants": {r["status"]: r["n"] for r in m}, "wallets": f["n"],
                    "float_total": int(f["s"]), "topup_volume": int(v["s"])})


# ---------------------------------------------------------------- public wallet API (v1)

@app.get("/v1/me")
@api_key_required
def v1_me():
    return jsonify(merchant_public(g.merchant))


@app.post("/v1/wallets")
@api_key_required
def v1_create_wallet():
    d = body()
    with tx() as cur:
        w = W.get_or_create_wallet(cur, g.merchant["id"], d.get("customer_id"), d.get("display_name"))
    return jsonify(W.wallet_out(w, g.merchant["currency"]))


@app.get("/v1/wallets/<customer_id>")
@api_key_required
def v1_get_wallet(customer_id):
    w = query("SELECT * FROM wallets WHERE merchant_id=%s AND customer_ref=%s",
              (g.merchant["id"], customer_id), one=True)
    if not w:
        raise ApiError(404, "wallet_not_found", "No wallet for this customer")
    return jsonify(W.wallet_out(w, g.merchant["currency"]))


@app.get("/v1/wallets/<customer_id>/transactions")
@api_key_required
def v1_wallet_transactions(customer_id):
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    w = query("SELECT id FROM wallets WHERE merchant_id=%s AND customer_ref=%s",
              (g.merchant["id"], customer_id), one=True)
    if not w:
        raise ApiError(404, "wallet_not_found", "No wallet for this customer")
    rows = query("SELECT * FROM ledger_entries WHERE wallet_id=%s ORDER BY id DESC LIMIT %s",
                 (w["id"], limit))
    return jsonify({"transactions": [W.entry_out(r) for r in rows]})


@app.post("/v1/wallets/<customer_id>/topups")
@api_key_required
def v1_create_topup(customer_id):
    d = body()
    m = g.merchant
    return_url = d.get("return_url")
    if return_url is not None and not valid_http_url(return_url):
        raise ApiError(400, "invalid_return_url", "return_url must be a full http(s) URL")
    with tx() as cur:
        w = W.get_or_create_wallet(cur, m["id"], customer_id)
        amount = W.check_topup_allowed(m, w, d.get("amount"))
        public_id = W.new_id("top")
        topup = {"public_id": public_id, "amount": amount, "currency": m["currency"]}
        provider_ref, checkout_url = payments.create_checkout(topup, m)
        cur.execute(
            """INSERT INTO topups (public_id, wallet_id, merchant_id, amount, currency, provider,
               provider_ref, return_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (public_id, w["id"], m["id"], amount, m["currency"], payments.provider_name(),
             provider_ref, return_url),
        )
    return jsonify({"id": public_id, "status": "pending", "amount": amount,
                    "currency": m["currency"], "checkout_url": checkout_url}), 201


def _sync_topup(public_id, merchant_id=None):
    """Load a top-up; if still pending, ask the gateway and credit when paid."""
    sql, params = "SELECT * FROM topups WHERE public_id=%s", [public_id]
    if merchant_id is not None:
        sql += " AND merchant_id=%s"
        params.append(merchant_id)
    t = query(sql, params, one=True)
    if not t:
        raise ApiError(404, "topup_not_found", "No top-up with that id")
    if t["status"] == "pending" and payments.is_paid(t):
        with tx() as cur:
            t = W.credit_topup(cur, t["id"])
    return t


def topup_out(t):
    return {"id": t["public_id"], "status": t["status"], "amount": t["amount"],
            "currency": t["currency"], "provider": t["provider"]}


@app.get("/v1/topups/<topup_id>")
@api_key_required
def v1_get_topup(topup_id):
    return jsonify(topup_out(_sync_topup(topup_id, g.merchant["id"])))


@app.post("/v1/wallets/<customer_id>/charges")
@api_key_required
def v1_charge(customer_id):
    d = body()
    idem = request.headers.get("Idempotency-Key")
    with tx() as cur:
        e = W.charge(cur, g.merchant, customer_id, d.get("amount"),
                     (d.get("reference") or None), (d.get("description") or None), idem)
        cur.execute("SELECT balance FROM wallets WHERE id=%s", (e["wallet_id"],))
        balance = cur.fetchone()["balance"]
    return jsonify({**W.entry_out(e), "balance": balance}), 201


@app.post("/v1/refunds")
@api_key_required
def v1_refund():
    d = body()
    idem = request.headers.get("Idempotency-Key")
    with tx() as cur:
        e = W.refund(cur, g.merchant, d.get("charge_id"), d.get("amount"), d.get("reason"), idem)
    return jsonify(W.entry_out(e)), 201


# ---------------------------------------------------------------- gateway pages & webhooks

MOCK_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mock Gateway</title><link rel="stylesheet" href="/static/css/style.css"></head>
<body class="center-page"><div class="card narrow">
<p class="eyebrow">Mock payment gateway · test mode</p>
<h1>Pay {{ amount }}</h1>
<p>Top-up for <b>{{ merchant }}</b> wallet <code>{{ customer }}</code>.</p>
{% if status == 'pending' %}
<form method="post"><button class="btn primary wide" name="result" value="pay">Pay now</button>
<button class="btn ghost wide" name="result" value="cancel">Cancel</button></form>
{% else %}<p>This top-up is already <b>{{ status }}</b>.</p>{% endif %}
</div></body></html>"""


def _money(amount, currency):
    return f"{amount / 100:,.2f} {currency.upper()}"


@app.route("/pay/mock/<topup_id>", methods=["GET", "POST"])
def mock_pay(topup_id):
    t = query("""SELECT t.*, m.business_name, w.customer_ref FROM topups t
                 JOIN merchants m ON m.id=t.merchant_id JOIN wallets w ON w.id=t.wallet_id
                 WHERE t.public_id=%s AND t.provider='mock'""", (topup_id,), one=True)
    if not t:
        return "Unknown top-up", 404
    if request.method == "POST" and t["status"] == "pending":
        if request.form.get("result") == "pay":
            with tx() as cur:
                W.credit_topup(cur, t["id"])
        else:
            query("UPDATE topups SET status='failed', completed_at=NOW() WHERE id=%s", (t["id"],))
        return redirect(f"/pay/return/{topup_id}")
    return render_template_string(MOCK_PAGE, amount=_money(t["amount"], t["currency"]),
                                  merchant=t["business_name"], customer=t["customer_ref"],
                                  status=t["status"])


@app.get("/pay/return/<topup_id>")
def pay_return(topup_id):
    t = _sync_topup(topup_id)
    if t["return_url"]:
        sep = "&" if "?" in t["return_url"] else "?"
        return redirect(t["return_url"] + sep + urlencode({"topup": t["public_id"],
                                                           "status": t["status"]}))
    return f"Top-up {t['public_id']} is {t['status']}. You can close this page."


@app.post("/webhooks/stripe")
def stripe_webhook():
    try:
        event = payments.parse_webhook(request.data, request.headers.get("Stripe-Signature", ""))
    except Exception:
        return "invalid signature", 400
    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        t = query("SELECT * FROM topups WHERE provider_ref=%s", (obj["id"],), one=True)
        if t and obj.get("payment_status") == "paid":
            with tx() as cur:
                W.credit_topup(cur, t["id"])
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
