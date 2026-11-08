import hashlib
import os
from datetime import date
import re
import secrets
from functools import wraps
from urllib.parse import quote_plus, urlencode, urlparse

from flask import Flask, g, jsonify, redirect, render_template_string, request, send_from_directory, session
from sqlalchemy import create_engine
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import checkout
import config
import mailer
import migrations
import payments
import pricing
import wallet as W
from db import query, tx
from errors import ApiError
from merchant_verification import Principal, init_verification

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.secret_key = config.SECRET_KEY
app.config.update(SESSION_COOKIE_NAME="hijasi_session", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=config.PRODUCTION)
if config.TRUST_PROXY:  # the hosting front-end terminates HTTPS and forwards the request
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if config.PRODUCTION:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith(("/api/", "/v1/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# ---------------------------------------------------------------- merchant verification module
# Teammate's review module (declarations, human review, audit trail), mounted at /verification.
# Its approval decision drives merchants.status, which gates API keys and the wallet API.

VERIFICATION_ENGINE = create_engine(
    f"mysql+pymysql://{quote_plus(config.DB_USER)}:{quote_plus(config.DB_PASSWORD)}"
    f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}?charset=utf8mb4",
    pool_pre_ping=True,
)


def resolve_principal():
    """Identity comes only from our server-side session, never from request data."""
    admin_path = request.path.startswith("/verification/admin")
    mid = session.get("merchant_id")
    if session.get("is_admin") and (admin_path or not mid):
        return Principal("platform-admin", "platform_admin", frozenset())
    if mid and not admin_path:
        m = query("SELECT id FROM merchants WHERE id=%s", (mid,), one=True)
        if m:
            return Principal(f"merchant-{m['id']}", "merchant_admin", frozenset({str(m["id"])}))
    return None


def verification_mutation_guard():
    sent, expected = request.headers.get("X-CSRF-Token", ""), session.get("csrf_token", "")
    return bool(sent and expected and secrets.compare_digest(sent, expected))


# Tables are created by seed.py and on server start (not at import, so seed.py can create the DB first).
init_verification(app, engine=VERIFICATION_ENGINE, resolve_principal=resolve_principal,
                  mutation_guard=verification_mutation_guard, create_tables=False)

STATUS_FROM_REVIEW = {"approved": "approved", "suspended": "suspended", "rejected": "rejected"}


def sync_merchant_status(merchant_id, review_status, reason):
    if not str(merchant_id or "").isdigit():
        return
    status = STATUS_FROM_REVIEW.get(review_status, "pending")
    with tx() as cur:
        cur.execute("UPDATE merchants SET status=%s, review_note=%s, reviewed_at=NOW() WHERE id=%s",
                    (status, (reason or "")[:255] or None, int(merchant_id)))
        if status != "approved":
            cur.execute("UPDATE api_keys SET revoked=1 WHERE merchant_id=%s", (int(merchant_id),))
    m = query("SELECT business_name, email FROM merchants WHERE id=%s", (int(merchant_id),), one=True)
    if m and review_status in ("approved", "rejected", "suspended", "needs_information"):
        texts = {
            "approved": ("Your Kassara account is approved",
                         "Good news: your business has been approved. You can now generate an API key and start integrating."),
            "rejected": ("Your Kassara application was not approved",
                         "We reviewed your application and could not approve it."),
            "suspended": ("Your Kassara account has been suspended",
                          "Your account has been suspended and its API key no longer works."),
            "needs_information": ("Kassara needs more information",
                                  "Our reviewer needs more information before your application can continue."),
        }
        subject, line = texts[review_status]
        lines = [f"Hi {m['business_name']},", line]
        if reason:
            lines.append(f"Reviewer's note: {reason}")
        mailer.send(m["email"], subject, lines, ("Open your dashboard", f"{config.PUBLIC_URL}/merchant"))


@app.after_request
def apply_review_decision(response):
    if (request.method == "POST" and response.status_code == 200
            and re.fullmatch(r"/verification/admin/applications/[^/]+/review", request.path)):
        data = response.get_json(silent=True) or {}
        sync_merchant_status(data.get("merchant_id"), data.get("status"), data.get("review_reason"))
    return response


BUSINESS_TYPES = {"farm", "grocery", "restaurant", "game_studio", "retail", "education", "services", "other"}
RISK_WORDS = ["crypto", "bitcoin", "token sale", "nft", "forex", "casino", "gambling", "betting",
              "loan", "remittance", "money transfer"]


# ---------------------------------------------------------------- helpers

@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify(e.to_dict()), e.status


# Dashboard requests that change data must echo the session's CSRF token.
CSRF_EXEMPT = {"/api/auth/login", "/api/auth/register", "/api/admin/login"}


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.before_request
def check_csrf():
    if (request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.path not in CSRF_EXEMPT):
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf_token", "")):
            raise ApiError(403, "csrf_failed", "Your session expired. Please reload the page.")


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
        "plan": m.get("plan") or pricing.DEFAULT_PLAN,
        "email_verified": bool(m.get("email_verified")),
        "review_note": m["review_note"],
        "policy": {"min_topup": m["min_topup"], "max_topup": m["max_topup"],
                   "max_balance": m["max_balance"]},
        "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
    }


VERIFY_TTL = 48  # hours


def send_verification_email(merchant_id):
    raw = secrets.token_urlsafe(32)
    with tx() as cur:
        cur.execute("""UPDATE merchants SET verify_token_hash=%s, verify_expires_at=NOW() + INTERVAL %s HOUR,
                       verify_sent_at=NOW() WHERE id=%s""", (hash_key(raw), VERIFY_TTL, merchant_id))
        cur.execute("SELECT business_name, email FROM merchants WHERE id=%s", (merchant_id,))
        m = cur.fetchone()
    mailer.send(m["email"], "Confirm your email for Kassara",
                [f"Hi {m['business_name']},",
                 "Please confirm your email address to continue your Kassara merchant application.",
                 f"This link expires in {VERIFY_TTL} hours. If you didn't sign up, ignore this email."],
                ("Confirm my email", f"{config.PUBLIC_URL}/verify-email?token={raw}"))


def notify_entry(entry_id):
    """Email the wallet holder a receipt for a ledger entry (if we have their email)."""
    if not entry_id:
        return
    e = query("""SELECT e.*, w.email AS wallet_email, w.display_name, w.balance AS wallet_balance,
                        m.business_name, m.currency
                 FROM ledger_entries e JOIN wallets w ON w.id=e.wallet_id JOIN merchants m ON m.id=e.merchant_id
                 WHERE e.id=%s""", (entry_id,), one=True)
    if not e or not e["wallet_email"]:
        return
    amt, bal = mailer.money(abs(e["amount"]), e["currency"]), mailer.money(e["balance_after"], e["currency"])
    shop = e["business_name"]
    greeting = f"Hi {e['display_name']}," if e["display_name"] else "Hi,"
    if e["type"] == "topup":
        subject, line = f"Receipt: {amt} added to your {shop} wallet", f"You topped up your {shop} wallet with {amt}."
        t = query("SELECT fee, total_charged FROM topups WHERE public_id=%s", (e["reference"],), one=True)
        if t and t["fee"]:
            line += (f" You paid {mailer.money(t['total_charged'], e['currency'])}, including a "
                     f"{mailer.money(t['fee'], e['currency'])} Kassara fee.")
    elif e["type"] == "charge":
        subject = f"Receipt: {amt} paid at {shop}"
        line = f"You paid {amt} from your {shop} wallet" + (f" for: {e['description']}." if e["description"] else ".")
    elif e["type"] == "refund":
        subject, line = f"Refund: {amt} back in your {shop} wallet", f"{shop} refunded {amt} to your wallet."
    else:
        return
    mailer.send(e["wallet_email"], subject,
                [greeting, line, f"New balance: {bal}", f"Transaction: {e['public_id']}",
                 f"Wallet credit can only be spent at {shop}."])


def credit_and_notify(topup_id):
    """Credit a confirmed top-up exactly once and email a receipt the first time."""
    with tx() as cur:
        cur.execute("SELECT status FROM topups WHERE id=%s FOR UPDATE", (topup_id,))
        was = cur.fetchone()["status"]
        t = W.credit_topup(cur, topup_id)
    if was != "succeeded" and t["status"] == "succeeded":
        notify_entry(t["ledger_entry_id"])
    return t


def effective_plan(merchant_id):
    """Current plan key; applies a scheduled downgrade once its date has arrived."""
    m = query("SELECT plan, plan_pending, plan_pending_from FROM merchants WHERE id=%s",
              (merchant_id,), one=True)
    if m["plan_pending"] and m["plan_pending_from"] and m["plan_pending_from"] <= date.today():
        query("UPDATE merchants SET plan=plan_pending, plan_pending=NULL, plan_pending_from=NULL "
              "WHERE id=%s AND plan_pending=%s", (merchant_id, m["plan_pending"]))
        return m["plan_pending"]
    return m["plan"] if m["plan"] in pricing.PLANS else pricing.DEFAULT_PLAN


def month_usage(merchant_id, cur=None):
    since = pricing.month_start()
    sql_t = "SELECT COUNT(*) AS n FROM ledger_entries WHERE merchant_id=%s AND created_at >= %s"
    sql_v = """SELECT COALESCE(SUM(amount),0) AS volume, COALESCE(SUM(fee),0) AS fees, COUNT(*) AS n
               FROM topups WHERE merchant_id=%s AND status='succeeded' AND completed_at >= %s"""
    if cur:
        cur.execute(sql_t, (merchant_id, since)); t = cur.fetchone()
        cur.execute(sql_v, (merchant_id, since)); v = cur.fetchone()
    else:
        t = query(sql_t, (merchant_id, since), one=True)
        v = query(sql_v, (merchant_id, since), one=True)
    return {"transactions": int(t["n"]), "topup_volume": int(v["volume"]),
            "topup_fees": int(v["fees"]), "topups": int(v["n"])}


def price_topup(m, amount, cur=None):
    """Server-side price breakdown for one top-up. Raises if the plan's volume allowance blocks it."""
    plan_key = effective_plan(m["id"])
    usage = month_usage(m["id"], cur)
    fee, label, blocked = pricing.topup_fee(plan_key, m["currency"], amount, usage["topup_volume"])
    if blocked:
        raise ApiError(402, "topup_volume_limit",
                       f"This business has reached its {pricing.plan(plan_key)['name']} plan's {blocked}")
    return {"plan": plan_key, "amount": amount, "fee": fee, "total": amount + fee,
            "currency": m["currency"], "fee_label": label,
            "note": "The customer's wallet is credited with the amount only. "
                    "Payment-processing fees of the payment provider are separate."}


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


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


@app.get("/demo")
def demo_shop():
    return redirect(config.DEMO_SHOP_URL)


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
    send_verification_email(mid)
    return jsonify({"ok": True, "csrf_token": csrf_token()}), 201


@app.get("/verify-email")
def verify_email():
    raw = request.args.get("token", "")
    m = query("SELECT id FROM merchants WHERE verify_token_hash=%s AND verify_expires_at > NOW()",
              (hash_key(raw),), one=True) if raw else None
    if not m:
        return redirect("/merchant#verify-failed")
    query("""UPDATE merchants SET email_verified=1, verify_token_hash=NULL, verify_expires_at=NULL
             WHERE id=%s""", (m["id"],))
    return redirect("/merchant#verified")


@app.post("/api/auth/resend-verification")
@login_required
def resend_verification():
    m = g.merchant
    if m.get("email_verified"):
        return jsonify({"ok": True, "already_verified": True})
    recent = query("SELECT verify_sent_at > NOW() - INTERVAL 60 SECOND AS recent FROM merchants WHERE id=%s",
                   (m["id"],), one=True)
    if recent and recent["recent"]:
        raise ApiError(429, "too_soon", "Please wait a minute before requesting another email")
    send_verification_email(m["id"])
    return jsonify({"ok": True})


@app.before_request
def require_verified_email_to_submit():
    """Applications can only be submitted for review once the merchant's email is confirmed."""
    if request.method == "POST" and re.fullmatch(r"/verification/merchants/\d+/application/submit", request.path):
        mid = session.get("merchant_id")
        m = query("SELECT email_verified FROM merchants WHERE id=%s", (mid,), one=True) if mid else None
        if m and not m["email_verified"]:
            raise ApiError(403, "email_not_verified", "Confirm your email address before submitting")


@app.post("/api/auth/login")
def login():
    d = body()
    m = query("SELECT * FROM merchants WHERE email=%s",
              ((d.get("email") or "").strip().lower(),), one=True)
    if not m or not check_password_hash(m["password_hash"], d.get("password") or ""):
        raise ApiError(401, "bad_credentials", "Wrong email or password")
    session["merchant_id"] = m["id"]
    return jsonify({"ok": True, "csrf_token": csrf_token()})


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
        "csrf_token": csrf_token(),
    })


@app.post("/api/keys")
@login_required
def roll_key():
    m = g.merchant
    if m["status"] != "approved":
        raise ApiError(403, "merchant_not_approved", "Your account must be approved first")
    if not m.get("email_verified"):
        raise ApiError(403, "email_not_verified", "Confirm your email address first")
    raw = "sk_test_" + secrets.token_urlsafe(24)
    with tx() as cur:
        cur.execute("UPDATE api_keys SET revoked=1 WHERE merchant_id=%s", (m["id"],))
        cur.execute("INSERT INTO api_keys (merchant_id, key_prefix, key_hash) VALUES (%s,%s,%s)",
                    (m["id"], raw[:12], hash_key(raw)))
    return jsonify({"secret_key": raw, "note": "Copy this now. It will not be shown again."}), 201


@app.get("/api/settings")
@login_required
def get_settings():
    return jsonify({"settings": merchant_public(g.merchant)})


@app.put("/api/settings")
@login_required
def update_settings():
    d = body()
    name = (d.get("business_name") or "").strip()
    if not name or len(name) > 150:
        raise ApiError(400, "invalid_name", "Business name is required (max 150 characters)")
    vals = {}
    for f in ("min_topup", "max_topup", "max_balance"):
        v = d.get(f)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ApiError(400, "invalid_policy", f"{f} must be a positive integer (minor units)")
        vals[f] = v
    if not vals["min_topup"] <= vals["max_topup"] <= vals["max_balance"]:
        raise ApiError(400, "invalid_policy", "Need min top-up <= max top-up <= max balance")
    query("UPDATE merchants SET business_name=%s, min_topup=%s, max_topup=%s, max_balance=%s "
          "WHERE id=%s",
          (name, vals["min_topup"], vals["max_topup"], vals["max_balance"], g.merchant["id"]))
    m = query("SELECT * FROM merchants WHERE id=%s", (g.merchant["id"],), one=True)
    return jsonify({"settings": merchant_public(m)})


@app.get("/api/overview")
@login_required
def merchant_overview():
    mid = g.merchant["id"]
    w = query("""SELECT COUNT(*) AS wallets, COALESCE(SUM(balance),0) AS float_total,
                 COALESCE(SUM(status='active'),0) AS active FROM wallets WHERE merchant_id=%s""",
              (mid,), one=True)
    sums = query("SELECT type, COUNT(*) AS n, COALESCE(SUM(amount),0) AS total "
                 "FROM ledger_entries WHERE merchant_id=%s GROUP BY type", (mid,))
    by = {s["type"]: {"count": s["n"], "total": int(s["total"])} for s in sums}
    activity = query("""SELECT DATE(created_at) AS day, COUNT(*) AS n FROM ledger_entries
                        WHERE merchant_id=%s AND created_at >= CURDATE() - INTERVAL 6 DAY
                        GROUP BY DATE(created_at)""", (mid,))
    return jsonify({
        "currency": g.merchant["currency"],
        "float_total": int(w["float_total"]),
        "wallets": w["wallets"],
        "active_wallets": int(w["active"]),
        "topped_up": by.get("topup", {}).get("total", 0),
        "spent": -by.get("charge", {}).get("total", 0) - by.get("refund", {}).get("total", 0),
        "transaction_count": sum(v["count"] for v in by.values()),
        "activity": [{"day": a["day"].isoformat(), "count": a["n"]} for a in activity],
    })


@app.get("/api/wallets")
@login_required
def merchant_wallets():
    q = (request.args.get("q") or "").strip()[:100]
    sql = """SELECT w.*, MAX(e.created_at) AS last_activity FROM wallets w
             LEFT JOIN ledger_entries e ON e.wallet_id = w.id
             WHERE w.merchant_id=%s"""
    params = [g.merchant["id"]]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " AND (w.customer_ref LIKE %s OR w.display_name LIKE %s)"
        params += [like, like]
    sql += " GROUP BY w.id ORDER BY w.id DESC LIMIT 200"
    rows = query(sql, params)
    return jsonify({"wallets": [
        {**W.wallet_out(r, g.merchant["currency"]),
         "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None}
        for r in rows]})


@app.get("/api/transactions")
@login_required
def merchant_transactions():
    t = request.args.get("type", "all")
    sql = """SELECT e.*, w.customer_ref, w.display_name FROM ledger_entries e
             JOIN wallets w ON w.id=e.wallet_id WHERE e.merchant_id=%s"""
    params = [g.merchant["id"]]
    if t in ("topup", "charge", "refund", "adjustment"):
        sql += " AND e.type=%s"
        params.append(t)
    sql += " ORDER BY e.id DESC LIMIT 200"
    rows = query(sql, params)
    return jsonify({"transactions": [
        {**W.entry_out(r), "customer_id": r["customer_ref"], "customer_name": r["display_name"]}
        for r in rows]})


# ---------------------------------------------------------------- admin API

@app.post("/api/admin/login")
def admin_login():
    d = body()
    if not secrets.compare_digest(str(d.get("token") or ""), config.ADMIN_TOKEN):
        raise ApiError(401, "bad_token", "Wrong admin token")
    session["is_admin"] = True
    return jsonify({"ok": True, "csrf_token": csrf_token()})


@app.get("/api/admin/merchants")
@admin_required
def admin_merchants():
    rows = query("SELECT * FROM merchants ORDER BY FIELD(status,'pending','approved','suspended',"
                 "'rejected'), created_at DESC")
    return jsonify({"merchants": [{**merchant_public(r), "risk_flags": risk_flags(r)} for r in rows],
                    "csrf_token": csrf_token()})


@app.get("/api/admin/stats")
@admin_required
def admin_stats():
    m = query("SELECT status, COUNT(*) AS n FROM merchants GROUP BY status")
    f = query("SELECT COALESCE(SUM(balance),0) AS s, COUNT(*) AS n FROM wallets", one=True)
    v = query("SELECT COALESCE(SUM(amount),0) AS s FROM ledger_entries WHERE type='topup'", one=True)
    a = query("SELECT status, COUNT(*) AS n FROM merchant_verification_applications GROUP BY status")
    return jsonify({"merchants": {r["status"]: r["n"] for r in m}, "wallets": f["n"],
                    "float_total": int(f["s"]), "topup_volume": int(v["s"]),
                    "applications": {r["status"]: r["n"] for r in a}})


# ---------------------------------------------------------------- public wallet API (v1)

@app.get("/v1/me")
@api_key_required
def v1_me():
    return jsonify(merchant_public(g.merchant))


@app.post("/v1/wallets")
@api_key_required
def v1_create_wallet():
    d = body()
    email = (d.get("email") or "").strip().lower() or None
    if email and (len(email) > 190 or not EMAIL_RE.fullmatch(email)):
        raise ApiError(400, "invalid_email", "email must be a valid email address")
    with tx() as cur:
        w = W.get_or_create_wallet(cur, g.merchant["id"], d.get("customer_id"), d.get("display_name"))
        if email:
            cur.execute("UPDATE wallets SET email=%s WHERE id=%s", (email, w["id"]))
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
        cur.execute("SELECT id FROM merchants WHERE id=%s FOR UPDATE", (m["id"],))  # serialise allowance checks
        w = W.get_or_create_wallet(cur, m["id"], customer_id)
        amount = W.check_topup_allowed(m, w, d.get("amount"))
        quote = price_topup(m, amount, cur)
        public_id = W.new_id("top")
        topup = {"public_id": public_id, "amount": amount, "fee": quote["fee"], "currency": m["currency"]}
        provider_ref, checkout_url = payments.create_checkout(topup, m)
        cur.execute(
            """INSERT INTO topups (public_id, wallet_id, merchant_id, amount, fee, total_charged, plan,
               currency, provider, provider_ref, return_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (public_id, w["id"], m["id"], amount, quote["fee"], quote["total"], quote["plan"],
             m["currency"], payments.provider_name(), provider_ref, return_url),
        )
    return jsonify({"id": public_id, "status": "pending", **quote, "checkout_url": checkout_url}), 201


@app.get("/v1/pricing/quote")
@api_key_required
def v1_quote():
    """Price breakdown to show the customer before they go to checkout."""
    m = g.merchant
    try:
        amount = int(request.args.get("amount", ""))
    except ValueError:
        raise ApiError(400, "invalid_amount", "amount must be a positive integer (minor units)")
    cid = request.args.get("customer_id")
    w = query("SELECT * FROM wallets WHERE merchant_id=%s AND customer_ref=%s", (m["id"], cid), one=True) \
        if cid else None
    amount = W.check_topup_allowed(m, w or {"balance": 0, "status": "active"}, amount)
    return jsonify(price_topup(m, amount))


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
        t = credit_and_notify(t["id"])
    return t


def topup_out(t):
    return {"id": t["public_id"], "status": t["status"], "amount": t["amount"],
            "fee": t.get("fee") or 0, "total": t.get("total_charged") or t["amount"],
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
        replay = bool(W._existing_by_idem(cur, g.merchant["id"], idem))
        e = W.charge(cur, g.merchant, customer_id, d.get("amount"),
                     (d.get("reference") or None), (d.get("description") or None), idem)
        cur.execute("SELECT balance FROM wallets WHERE id=%s", (e["wallet_id"],))
        balance = cur.fetchone()["balance"]
    if not replay:
        notify_entry(e["id"])
    return jsonify({**W.entry_out(e), "balance": balance}), 201


@app.post("/v1/refunds")
@api_key_required
def v1_refund():
    d = body()
    idem = request.headers.get("Idempotency-Key")
    with tx() as cur:
        replay = bool(W._existing_by_idem(cur, g.merchant["id"], idem))
        e = W.refund(cur, g.merchant, d.get("charge_id"), d.get("amount"), d.get("reason"), idem)
    if not replay:
        notify_entry(e["id"])
    return jsonify(W.entry_out(e)), 201


# ---------------------------------------------------------------- gateway pages & webhooks

MOCK_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mock Gateway</title><link rel="stylesheet" href="/static/css/style.css"></head>
<body class="center-page"><div class="card narrow">
<p class="eyebrow">Mock payment gateway · test mode</p>
<h1>Pay {{ total }}</h1>
<p>Top-up for <b>{{ merchant }}</b> wallet <code>{{ customer }}</code>.</p>
<p>Wallet credit {{ amount }}<br>Kassara fee {{ fee }}<br><b>Total {{ total }}</b></p>
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
            credit_and_notify(t["id"])
        else:
            query("UPDATE topups SET status='failed', completed_at=NOW() WHERE id=%s", (t["id"],))
        return redirect(f"/pay/return/{topup_id}")
    return render_template_string(MOCK_PAGE, amount=_money(t["amount"], t["currency"]),
                                  fee=_money(t["fee"] or 0, t["currency"]),
                                  total=_money(t["total_charged"] or t["amount"], t["currency"]),
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
            credit_and_notify(t["id"])
    return "ok"


# ---------------------------------------------------------------- plans & billing

@app.get("/api/pricing")
def public_pricing():
    return jsonify({"plans": pricing.public_plans(), "status": "proposed",
                    "note": "Proposed launch prices. Payment-processing fees are charged separately "
                            "by the payment provider. Top-up volume allowances for subscription "
                            "plans are not final yet."})


def billing_out(mid):
    m = query("SELECT * FROM merchants WHERE id=%s", (mid,), one=True)
    plan_key = effective_plan(mid)
    m = query("SELECT * FROM merchants WHERE id=%s", (mid,), one=True)
    p = pricing.plan(plan_key)
    usage = month_usage(mid)
    extra, extra_cost = pricing.overage_cost(plan_key, usage["transactions"])
    history = query("SELECT from_plan, to_plan, effective_on, kind, created_at FROM plan_changes "
                    "WHERE merchant_id=%s ORDER BY id DESC LIMIT 10", (mid,))
    return {
        "plan": plan_key,
        "pending_plan": m["plan_pending"],
        "pending_from": m["plan_pending_from"].isoformat() if m["plan_pending_from"] else None,
        "plans": pricing.public_plans(),
        "period": {"start": pricing.month_start().isoformat(), "end": pricing.next_month_start().isoformat()},
        "usage": {**usage,
                  "included_transactions": p["included_transactions"],
                  "extra_transactions": extra,
                  "topup_volume_allowance": p["topup_volume_allowance"]},
        "estimate": {"currency": pricing.PRICING_CURRENCY, "subscription": p["monthly_price"],
                     "transaction_overage": extra_cost, "total": p["monthly_price"] + extra_cost},
        "history": [{**h, "effective_on": h["effective_on"].isoformat(),
                     "created_at": h["created_at"].isoformat()} for h in history],
    }


@app.get("/api/billing")
@login_required
def get_billing():
    return jsonify(billing_out(g.merchant["id"]))


@app.put("/api/billing/plan")
@login_required
def change_plan():
    """Upgrades start immediately; downgrades start on the 1st of next month.
    Choosing the current plan cancels a scheduled downgrade."""
    target = body().get("plan")
    if target not in pricing.PLANS:
        raise ApiError(400, "invalid_plan", f"plan must be one of: {', '.join(pricing.ORDER)}")
    mid = g.merchant["id"]
    current = effective_plan(mid)
    with tx() as cur:
        cur.execute("SELECT plan_pending FROM merchants WHERE id=%s FOR UPDATE", (mid,))
        pending = cur.fetchone()["plan_pending"]
        if target == current:
            if pending:
                cur.execute("UPDATE merchants SET plan_pending=NULL, plan_pending_from=NULL WHERE id=%s", (mid,))
                cur.execute("""INSERT INTO plan_changes (merchant_id, from_plan, to_plan, effective_on, kind)
                               VALUES (%s,%s,%s,%s,'cancel_downgrade')""", (mid, pending, current, date.today()))
        elif pricing.ORDER.index(target) > pricing.ORDER.index(current):
            cur.execute("UPDATE merchants SET plan=%s, plan_pending=NULL, plan_pending_from=NULL WHERE id=%s",
                        (target, mid))
            cur.execute("""INSERT INTO plan_changes (merchant_id, from_plan, to_plan, effective_on, kind)
                           VALUES (%s,%s,%s,%s,'upgrade')""", (mid, current, target, date.today()))
        else:
            start = pricing.next_month_start()
            cur.execute("UPDATE merchants SET plan_pending=%s, plan_pending_from=%s WHERE id=%s",
                        (target, start, mid))
            cur.execute("""INSERT INTO plan_changes (merchant_id, from_plan, to_plan, effective_on, kind)
                           VALUES (%s,%s,%s,%s,'downgrade')""", (mid, current, target, start))
    return jsonify(billing_out(mid))


checkout.register(app, api_key_required, notify_entry)


if __name__ == "__main__":
    from merchant_verification.models import Base as VerificationBase
    VerificationBase.metadata.create_all(VERIFICATION_ENGINE)
    checkout.ensure_tables()
    migrations.run()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=not config.PRODUCTION)
