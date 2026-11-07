"""Green Valley Farm — a demo merchant shop that integrates with the HIJASI wallet API.

This is a completely separate app from HIJASI. It talks to HIJASI only over the
public REST API, using its secret key from the server side — exactly how a real
merchant would integrate.
"""
import os
import re
import uuid

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

HIJASI_URL = os.getenv("HIJASI_URL", "http://127.0.0.1:5000").rstrip("/").replace("://localhost", "://127.0.0.1")
HIJASI_KEY = os.getenv("HIJASI_SECRET_KEY", "")
FARM_URL = os.getenv("FARM_URL", "http://127.0.0.1:5001").rstrip("/")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("FARM_SECRET_KEY", "farm-dev-secret")
# Both apps run on localhost; cookies ignore the port, so use a distinct cookie name.
app.config.update(SESSION_COOKIE_NAME="farm_session", SESSION_COOKIE_SAMESITE="Lax")

# Prices in minor units of the merchant's currency.
PRODUCTS = [
    {"id": "veg-box", "name": "Weekly veg box", "unit": "box", "price": 2490, "emoji": "🥬"},
    {"id": "eggs", "name": "Free-range eggs", "unit": "12 pcs", "price": 590, "emoji": "🥚"},
    {"id": "milk", "name": "Fresh milk", "unit": "2 L", "price": 380, "emoji": "🥛"},
    {"id": "tomatoes", "name": "Tomatoes", "unit": "1 kg", "price": 450, "emoji": "🍅"},
    {"id": "potatoes", "name": "Potatoes", "unit": "2 kg", "price": 520, "emoji": "🥔"},
    {"id": "honey", "name": "Wildflower honey", "unit": "500 g", "price": 990, "emoji": "🍯"},
]
PRODUCT_BY_ID = {p["id"]: p for p in PRODUCTS}


def hijasi(method, path, json=None, idem=None):
    headers = {"Authorization": f"Bearer {HIJASI_KEY}"}
    if idem:
        headers["Idempotency-Key"] = idem
    r = requests.request(method, HIJASI_URL + path, json=json, headers=headers, timeout=15)
    try:
        data = r.json()
    except ValueError:
        data = {"error": {"message": r.text[:200]}}
    return r.status_code, data


def passthrough(status, data):
    return jsonify(data), status


def customer_id():
    cid = session.get("customer_id")
    if not cid:
        return None
    return cid


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/products")
def products():
    status, me = hijasi("GET", "/v1/me")
    currency = me.get("currency", "eur") if status == 200 else "eur"
    return jsonify({"products": PRODUCTS, "currency": currency,
                    "policy": me.get("policy") if status == 200 else None})


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()[:80]
    phone = re.sub(r"\D", "", d.get("phone") or "")
    if not name or len(phone) < 6:
        return jsonify({"error": {"message": "Enter your name and phone number"}}), 400
    cid = f"cust_{phone}"
    status, data = hijasi("POST", "/v1/wallets", {"customer_id": cid, "display_name": name})
    if status != 200:
        return passthrough(status, data)
    session["customer_id"] = cid
    session["name"] = name
    return jsonify({"ok": True})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/wallet")
def wallet():
    cid = customer_id()
    if not cid:
        return jsonify({"error": {"message": "Not logged in"}}), 401
    s1, w = hijasi("GET", f"/v1/wallets/{cid}")
    if s1 != 200:
        return passthrough(s1, w)
    _, t = hijasi("GET", f"/v1/wallets/{cid}/transactions?limit=20")
    return jsonify({"name": session.get("name"), "wallet": w,
                    "transactions": t.get("transactions", [])})


@app.post("/api/topup")
def topup():
    cid = customer_id()
    if not cid:
        return jsonify({"error": {"message": "Not logged in"}}), 401
    d = request.get_json(silent=True) or {}
    status, data = hijasi("POST", f"/v1/wallets/{cid}/topups",
                          {"amount": d.get("amount"), "return_url": f"{FARM_URL}/"})
    return passthrough(status, data)


@app.get("/api/topup/<topup_id>")
def topup_status(topup_id):
    if not customer_id():
        return jsonify({"error": {"message": "Not logged in"}}), 401
    return passthrough(*hijasi("GET", f"/v1/topups/{topup_id}"))


@app.post("/api/checkout")
def checkout():
    """Create a HIJASI hosted wallet checkout; the customer confirms the payment there."""
    cid = customer_id()
    if not cid:
        return jsonify({"error": {"message": "Not logged in"}}), 401
    d = request.get_json(silent=True) or {}
    items, total, lines = d.get("items") or [], 0, []
    for it in items:
        p = PRODUCT_BY_ID.get(it.get("id"))
        qty = it.get("qty")
        if not p or not isinstance(qty, int) or not 1 <= qty <= 50:
            return jsonify({"error": {"message": "Invalid cart"}}), 400
        total += p["price"] * qty   # price is decided by the server, never the browser
        lines.append(f"{qty}× {p['name']}")
    if total <= 0:
        return jsonify({"error": {"message": "Cart is empty"}}), 400
    # The browser sends a per-checkout key so a double-click never creates two checkouts.
    order_id = "order-" + re.sub(r"[^a-zA-Z0-9-]", "", str(d.get("checkout_id") or uuid.uuid4()))[:40]
    status, data = hijasi("POST", "/v1/checkout_sessions", {
        "customer_id": cid, "amount": total, "reference": order_id,
        "description": ", ".join(lines)[:255],
        "success_url": f"{FARM_URL}/", "cancel_url": f"{FARM_URL}/",
    }, idem=order_id)
    if status in (200, 201):
        session["pending_checkout"] = data["id"]
    return passthrough(status, data)


@app.get("/api/checkout/<checkout_id>")
def checkout_status(checkout_id):
    """Verify the result server-side; never trust the ?status= in the return URL."""
    if not customer_id() or session.get("pending_checkout") != checkout_id:
        return jsonify({"error": {"message": "Unknown checkout"}}), 404
    status, data = hijasi("GET", f"/v1/checkout_sessions/{checkout_id}")
    if status == 200 and data.get("status") != "open":
        session.pop("pending_checkout", None)
    return passthrough(status, data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=True)
