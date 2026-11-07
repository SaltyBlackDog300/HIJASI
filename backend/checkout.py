"""Kassara hosted wallet checkout ("Pay with wallet").

The merchant's server creates a checkout session; the customer is redirected to
our hosted page, confirms with a one-time code, and only then is the wallet
charged. Card data never touches this page — it only spends prepaid balance.

Security properties:
  * Session ids are long random tokens; the amount and redirect URLs are fixed
    server-side when the merchant creates the session.
  * Sessions expire after 15 minutes and can be paid exactly once.
  * The customer must enter a 6-digit one-time code (5-minute lifetime,
    stored only as an HMAC). 5 wrong attempts lock the session; max 3 codes.
  * Redirects only go to the merchant's registered website host.
  * Pages send a strict CSP, cannot be framed, are never cached, and every
    form POST carries a CSRF token tied to the browser session.
  * The charge itself goes through the same ledger (row lock, idempotency key =
    session id), so double-submits cannot charge twice.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import abort, g, jsonify, redirect, render_template_string, request, session

import config
import wallet as W
from db import query, tx
from errors import ApiError

SESSION_TTL = timedelta(minutes=15)
CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_CODES = 3

CHECKOUT_DDL = """
CREATE TABLE IF NOT EXISTS checkout_sessions (
  id               VARCHAR(48) PRIMARY KEY,           -- cs_<random>, also the URL token
  merchant_id      INT NOT NULL,
  wallet_id        INT NOT NULL,
  amount           BIGINT NOT NULL,
  currency         VARCHAR(3) NOT NULL,
  reference        VARCHAR(150),
  description      VARCHAR(255),
  success_url      VARCHAR(500) NOT NULL,
  cancel_url       VARCHAR(500) NOT NULL,
  status           ENUM('open','paid','cancelled','expired','locked') NOT NULL DEFAULT 'open',
  idempotency_key  VARCHAR(100) NULL,
  code_hash        CHAR(64) NULL,
  code_expires_at  DATETIME NULL,
  code_sends       INT NOT NULL DEFAULT 0,
  code_attempts    INT NOT NULL DEFAULT 0,
  ledger_entry_id  BIGINT NULL,
  created_at       DATETIME NOT NULL,
  expires_at       DATETIME NOT NULL,
  completed_at     DATETIME NULL,
  UNIQUE KEY uq_cs_idem (merchant_id, idempotency_key),
  FOREIGN KEY (merchant_id) REFERENCES merchants(id),
  FOREIGN KEY (wallet_id) REFERENCES wallets(id)
) ENGINE=InnoDB
"""


def ensure_tables():
    query(CHECKOUT_DDL)


# ---------------------------------------------------------------- helpers

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _host(url):
    try:
        u = urlparse(url)
        return u.hostname if u.scheme in ("http", "https") and u.hostname else None
    except ValueError:
        return None


def redirect_allowed(merchant, url):
    """Only the merchant's registered website host (loopback hosts count as one for local dev)."""
    target, site = _host(url), _host(merchant["website"])
    if not target or not site:
        return False
    return target == site or (target in LOOPBACK and site in LOOPBACK)


def _code_hash(sid, code):
    return hmac.new(config.SECRET_KEY.encode(), f"{sid}:{code}".encode(), hashlib.sha256).hexdigest()


def _now():
    return datetime.now().replace(microsecond=0)


def _with_params(url, **params):
    return url + ("&" if "?" in url else "?") + urlencode(params)


def session_out(s):
    return {
        "id": s["id"], "status": s["status"], "amount": s["amount"], "currency": s["currency"],
        "reference": s["reference"], "url": f"{config.PUBLIC_URL}/checkout/{s['id']}",
        "expires_at": s["expires_at"].isoformat(), "transaction_id": s.get("txn_public_id"),
    }


def _load(sid, lock=False, cur=None):
    sql = """SELECT cs.*, m.business_name, m.website, w.customer_ref, w.display_name, w.balance,
                    w.status AS wallet_status, e.public_id AS txn_public_id
             FROM checkout_sessions cs
             JOIN merchants m ON m.id = cs.merchant_id
             JOIN wallets w ON w.id = cs.wallet_id
             LEFT JOIN ledger_entries e ON e.id = cs.ledger_entry_id
             WHERE cs.id = %s"""
    if lock:
        cur.execute(sql + " FOR UPDATE", (sid,))
        return cur.fetchone()
    return query(sql, (sid,), one=True)


def _expire_if_needed(cur, s):
    if s["status"] == "open" and s["expires_at"] <= _now():
        cur.execute("UPDATE checkout_sessions SET status='expired' WHERE id=%s", (s["id"],))
        s["status"] = "expired"
    return s


# ---------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Pay {{ amount }} — Kassara Checkout</title>
<link rel="stylesheet" href="/static/css/checkout.css">
</head><body>
<main class="co">
  <header class="co-head">
    <span class="co-brand"><span class="mark">◈</span> kassara <span class="lock">🔒 Secure checkout</span></span>
  </header>
  <section class="co-card">
    <p class="co-merchant">{{ s.business_name }}</p>
    <h1>{{ amount }}</h1>
    {% if s.description %}<p class="co-desc">{{ s.description }}</p>{% endif %}
    <dl class="co-meta">
      <dt>Paying from</dt><dd>{{ s.display_name or s.customer_ref }}'s {{ s.business_name }} wallet</dd>
      {% if s.reference %}<dt>Order</dt><dd>{{ s.reference }}</dd>{% endif %}
      {% if s.status == 'open' %}<dt>Balance after</dt><dd>{{ balance_after }}</dd>{% endif %}
    </dl>

    {% if message %}<p class="co-msg {{ message_kind }}">{{ message }}</p>{% endif %}

    {% if s.status == 'open' and not enough %}
      <p class="co-msg error">Your wallet balance ({{ balance }}) is too low for this payment. Top up at {{ s.business_name }} first.</p>
      <form method="post" action="/checkout/{{ s.id }}/cancel"><input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn ghost">Return to {{ s.business_name }}</button></form>

    {% elif s.status == 'open' and not code_sent %}
      <p class="co-help">To protect your wallet, we'll send a one-time code to confirm this payment.</p>
      <form method="post" action="/checkout/{{ s.id }}/code"><input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn primary">Send confirmation code</button></form>
      <form method="post" action="/checkout/{{ s.id }}/cancel"><input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn ghost">Cancel and return</button></form>

    {% elif s.status == 'open' %}
      {% if demo_code %}<p class="co-demo"><b>Demo mode:</b> in production this code is sent by SMS. Your code is <code>{{ demo_code }}</code></p>{% endif %}
      <form method="post" action="/checkout/{{ s.id }}/confirm" class="co-form" autocomplete="off">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <label for="code">6-digit confirmation code</label>
        <input id="code" name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autofocus>
        <button class="btn primary">Pay {{ amount }}</button>
      </form>
      <div class="co-row">
        {% if can_resend %}<form method="post" action="/checkout/{{ s.id }}/code"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="link">Send a new code</button></form>{% endif %}
        <form method="post" action="/checkout/{{ s.id }}/cancel"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="link">Cancel</button></form>
      </div>
      <p class="co-fine">{{ attempts_left }} attempt{{ '' if attempts_left == 1 else 's' }} left · expires {{ expires }}</p>

    {% elif s.status == 'paid' %}
      <p class="co-msg ok">Payment complete. Transaction {{ s.txn_public_id }}.</p>
      <a class="btn primary" href="{{ return_url }}">Return to {{ s.business_name }}</a>

    {% else %}
      <p class="co-msg error">{{ closed_text }}</p>
      <a class="btn ghost" href="{{ return_url }}">Return to {{ s.business_name }}</a>
    {% endif %}
  </section>
  <footer class="co-foot">Wallet credit can only be spent at {{ s.business_name }}. Kassara never asks for card details on this page.</footer>
</main>
</body></html>"""

CLOSED_TEXT = {
    "cancelled": "This payment was cancelled.",
    "expired": "This checkout has expired. Start the payment again from the shop.",
    "locked": "Too many wrong codes. This checkout is locked for your protection.",
}


def _render(s, message=None, kind="info", demo_code=None):
    money = lambda v: f"{v / 100:,.2f} {s['currency'].upper()}"
    paid = s["status"] == "paid"
    return_url = _with_params(s["success_url"] if paid else s["cancel_url"],
                              checkout=s["id"], status=s["status"])
    html = render_template_string(
        PAGE, s=s, amount=money(s["amount"]), balance=money(s["balance"]),
        balance_after=money(max(s["balance"] - s["amount"], 0)), enough=s["balance"] >= s["amount"],
        code_sent=bool(s["code_hash"]), can_resend=s["code_sends"] < MAX_CODES,
        attempts_left=MAX_ATTEMPTS - s["code_attempts"],
        expires=s["expires_at"].strftime("%H:%M"), message=message, message_kind=kind,
        demo_code=demo_code, csrf=_csrf(), return_url=return_url,
        closed_text=CLOSED_TEXT.get(s["status"], ""))
    return html


def _csrf():
    if "checkout_csrf" not in session:
        session["checkout_csrf"] = secrets.token_urlsafe(24)
    return session["checkout_csrf"]


def _check_csrf():
    sent, expected = request.form.get("csrf", ""), session.get("checkout_csrf", "")
    if not sent or not expected or not secrets.compare_digest(sent, expected):
        abort(403)


# ---------------------------------------------------------------- routes

def register(app, api_key_required):

    @app.after_request
    def checkout_headers(resp):
        if request.path.startswith("/checkout/"):
            resp.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self' http: https:; "
                "frame-ancestors 'none'; base-uri 'none'")
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "no-referrer"
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- merchant API (server-to-server)

    @app.post("/v1/checkout_sessions")
    @api_key_required
    def create_checkout_session():
        d = request.get_json(silent=True)
        if not isinstance(d, dict):
            raise ApiError(400, "invalid_json", "Request body must be a JSON object")
        m = g.merchant
        for f in ("success_url", "cancel_url"):
            if not isinstance(d.get(f), str) or not redirect_allowed(m, d[f]):
                raise ApiError(400, "invalid_redirect",
                               f"{f} must be an http(s) URL on your registered website ({m['website']})")
        idem = request.headers.get("Idempotency-Key") or None
        if idem:
            existing = query("SELECT id FROM checkout_sessions WHERE merchant_id=%s AND idempotency_key=%s",
                             (m["id"], idem[:100]), one=True)
            if existing:
                return jsonify(session_out(_load(existing["id"])))
        amount = d.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ApiError(400, "invalid_amount", "amount must be a positive integer in minor units")
        sid = "cs_" + secrets.token_urlsafe(30)
        now = _now()
        with tx() as cur:
            w = W.get_or_create_wallet(cur, m["id"], d.get("customer_id"))
            cur.execute(
                """INSERT INTO checkout_sessions (id, merchant_id, wallet_id, amount, currency, reference,
                   description, success_url, cancel_url, idempotency_key, created_at, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sid, m["id"], w["id"], amount, m["currency"], (d.get("reference") or None),
                 (str(d.get("description") or "")[:255] or None), d["success_url"], d["cancel_url"],
                 idem[:100] if idem else None, now, now + SESSION_TTL))
        return jsonify(session_out(_load(sid))), 201

    @app.get("/v1/checkout_sessions/<sid>")
    @api_key_required
    def get_checkout_session(sid):
        s = _load(sid)
        if not s or s["merchant_id"] != g.merchant["id"]:
            raise ApiError(404, "checkout_not_found", "No checkout session with that id")
        if s["status"] == "open" and s["expires_at"] <= _now():
            with tx() as cur:
                _expire_if_needed(cur, s)
        return jsonify(session_out(s))

    # ---- hosted page (customer's browser)

    @app.get("/checkout/<sid>")
    def checkout_page(sid):
        s = _load(sid)
        if not s:
            abort(404)
        with tx() as cur:
            _expire_if_needed(cur, s)
        return _render(s)

    @app.post("/checkout/<sid>/code")
    def checkout_send_code(sid):
        _check_csrf()
        with tx() as cur:
            s = _load(sid, lock=True, cur=cur)
            if not s:
                abort(404)
            _expire_if_needed(cur, s)
            if s["status"] != "open" or s["balance"] < s["amount"]:
                return _render(s)
            if s["code_sends"] >= MAX_CODES:
                return _render(s, "You've reached the maximum number of codes for this checkout.", "error")
            code = f"{secrets.randbelow(10**6):06d}"
            cur.execute("""UPDATE checkout_sessions SET code_hash=%s, code_expires_at=%s,
                           code_sends=code_sends+1 WHERE id=%s""",
                        (_code_hash(sid, code), _now() + CODE_TTL, sid))
            s.update(code_hash="x", code_sends=s["code_sends"] + 1)
        # Production: send `code` by SMS/email to the wallet holder. Demo: show it on the page.
        return _render(s, "We sent a 6-digit code. It is valid for 5 minutes.", "info",
                       demo_code=code if config.DEMO_SHOW_CODES else None)

    @app.post("/checkout/<sid>/confirm")
    def checkout_confirm(sid):
        _check_csrf()
        code = (request.form.get("code") or "").strip()
        with tx() as cur:
            s = _load(sid, lock=True, cur=cur)
            if not s:
                abort(404)
            _expire_if_needed(cur, s)
            if s["status"] != "open" or not s["code_hash"]:
                return _render(s)
            if s["code_expires_at"] <= _now():
                return _render(s, "That code has expired. Send a new one.", "error")
            if not (len(code) == 6 and code.isdigit()
                    and hmac.compare_digest(_code_hash(sid, code), s["code_hash"])):
                attempts = s["code_attempts"] + 1
                status = "locked" if attempts >= MAX_ATTEMPTS else "open"
                cur.execute("UPDATE checkout_sessions SET code_attempts=%s, status=%s WHERE id=%s",
                            (attempts, status, sid))
                s.update(code_attempts=attempts, status=status)
                return _render(s, None if status == "locked" else "Wrong code. Please try again.", "error")
            merchant = query("SELECT * FROM merchants WHERE id=%s", (s["merchant_id"],), one=True)
            if merchant["status"] != "approved":
                return _render(s, "This merchant can't accept payments right now.", "error")
            try:
                entry = W.charge(cur, merchant, s["customer_ref"], s["amount"], s["reference"],
                                 s["description"] or "Wallet checkout", idem_key=sid)
            except ApiError as e:
                return _render(s, e.message, "error")
            cur.execute("""UPDATE checkout_sessions SET status='paid', ledger_entry_id=%s, completed_at=%s,
                           code_hash=NULL WHERE id=%s""", (entry["id"], _now(), sid))
        return redirect(_with_params(s["success_url"], checkout=sid, status="paid"), code=303)

    @app.post("/checkout/<sid>/cancel")
    def checkout_cancel(sid):
        _check_csrf()
        with tx() as cur:
            s = _load(sid, lock=True, cur=cur)
            if not s:
                abort(404)
            if s["status"] == "open":
                cur.execute("UPDATE checkout_sessions SET status='cancelled', code_hash=NULL WHERE id=%s", (sid,))
                s["status"] = "cancelled"
        if s["status"] == "paid":
            return _render(s)
        return redirect(_with_params(s["cancel_url"], checkout=sid, status=s["status"]), code=303)
