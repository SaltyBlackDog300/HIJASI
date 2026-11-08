"""Merchant webhooks: Kassara tells the merchant's server when money moves.

Each event is stored first (webhook_events), then POSTed to the merchant's webhook URL as JSON,
signed with the merchant's signing secret:

    Kassara-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

A 2xx response marks the event delivered. Failed events are retried (up to MAX_ATTEMPTS, with
back-off) whenever the merchant receives a newer event, and can be resent from the dashboard.
Delivery runs in a background thread so it never slows down the customer's payment.
"""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import sys
import threading
import time
from urllib.parse import urlparse

import requests

import config
from db import query

MAX_ATTEMPTS = 6
TIMEOUT = 6
EVENT_TYPES = ("wallet.topup.succeeded", "wallet.charge.succeeded", "wallet.refund.succeeded",
               "checkout_session.completed", "ping")

DDL = """CREATE TABLE IF NOT EXISTS webhook_events (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  public_id    VARCHAR(40) NOT NULL UNIQUE,
  merchant_id  INT NOT NULL,
  type         VARCHAR(60) NOT NULL,
  payload      MEDIUMTEXT NOT NULL,
  status       ENUM('pending','delivered','failed') NOT NULL DEFAULT 'pending',
  attempts     INT NOT NULL DEFAULT 0,
  last_code    INT NULL,
  last_error   VARCHAR(255) NULL,
  next_retry_at DATETIME NULL,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  delivered_at DATETIME NULL,
  KEY ix_merchant (merchant_id, id),
  KEY ix_retry (status, next_retry_at)
) ENGINE=InnoDB"""


def _log(msg):
    print(f"[kassara-webhooks] {msg}", file=sys.stderr, flush=True)


def new_secret():
    return "whsec_" + secrets.token_urlsafe(24)


def sign(secret, timestamp, body):
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def url_problem(url):
    """Return why a webhook URL is not allowed, or None. Blocks internal addresses (SSRF)."""
    try:
        u = urlparse(url)
    except Exception:
        return "Enter a full URL"
    if u.scheme not in ("https", "http") or not u.hostname:
        return "Enter a full URL starting with https://"
    if not config.PRODUCTION:
        return None  # local development may use http://127.0.0.1 for the demo shop
    if u.scheme != "https":
        return "Webhook URLs must use https://"
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(u.hostname, u.port or 443)}
    except socket.gaierror:
        return "That host name does not resolve"
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "Webhook URLs must point to a public address"
    return None


def emit(merchant_id, event_type, data):
    """Record an event and deliver it in the background. Never raises."""
    try:
        m = query("SELECT webhook_url FROM merchants WHERE id=%s", (merchant_id,), one=True)
        if not m or not m.get("webhook_url"):
            return None
        public_id = "evt_" + secrets.token_hex(12)
        payload = {"id": public_id, "type": event_type, "livemode": False,
                   "created": int(time.time()), "data": data}
        query("INSERT INTO webhook_events (public_id, merchant_id, type, payload) VALUES (%s,%s,%s,%s)",
              (public_id, merchant_id, event_type, json.dumps(payload, separators=(",", ":"))))
        threading.Thread(target=_deliver_batch, args=(merchant_id, public_id), daemon=True).start()
        return public_id
    except Exception as exc:
        _log(f"emit failed merchant={merchant_id} type={event_type}: {exc}")
        return None


def _deliver_batch(merchant_id, public_id):
    deliver(public_id)
    # opportunistic retry of this merchant's earlier failures that are due
    due = query("""SELECT public_id FROM webhook_events WHERE merchant_id=%s AND status='failed'
                   AND attempts < %s AND next_retry_at <= NOW() ORDER BY id LIMIT 5""",
                (merchant_id, MAX_ATTEMPTS))
    for row in due:
        deliver(row["public_id"])


def deliver(public_id):
    """POST one event to the merchant's current webhook URL. Returns the updated event row."""
    try:
        ev = query("""SELECT e.*, m.webhook_url, m.webhook_secret FROM webhook_events e
                      JOIN merchants m ON m.id=e.merchant_id WHERE e.public_id=%s""", (public_id,), one=True)
        if not ev:
            return None
        code, error = None, None
        if not ev["webhook_url"]:
            error = "No webhook URL set"
        elif url_problem(ev["webhook_url"]):
            error = url_problem(ev["webhook_url"])
        else:
            body = ev["payload"].encode()
            ts = int(time.time())
            try:
                r = requests.post(ev["webhook_url"], data=body, timeout=TIMEOUT, allow_redirects=False,
                                  headers={"Content-Type": "application/json",
                                           "User-Agent": "Kassara-Webhooks/1.0",
                                           "Kassara-Event": ev["type"],
                                           "Kassara-Event-Id": ev["public_id"],
                                           "Kassara-Signature": sign(ev["webhook_secret"] or "", ts, body)})
                code = r.status_code
                if not 200 <= code < 300:
                    error = f"Endpoint answered HTTP {code}"
            except requests.RequestException as exc:
                error = f"Could not reach endpoint: {type(exc).__name__}"
        attempts = ev["attempts"] + 1
        if error is None:
            query("""UPDATE webhook_events SET status='delivered', attempts=%s, last_code=%s, last_error=NULL,
                     next_retry_at=NULL, delivered_at=NOW() WHERE id=%s""", (attempts, code, ev["id"]))
        else:
            backoff = min(60 * 2 ** attempts, 6 * 3600)  # 2 min, 4 min, 8 min ... up to 6 h
            query("""UPDATE webhook_events SET status='failed', attempts=%s, last_code=%s, last_error=%s,
                     next_retry_at=NOW() + INTERVAL %s SECOND WHERE id=%s""",
                  (attempts, code, error[:255], backoff, ev["id"]))
        return query("SELECT * FROM webhook_events WHERE id=%s", (ev["id"],), one=True)
    except Exception as exc:
        _log(f"deliver failed {public_id}: {exc}")
        return None


def event_out(e):
    return {"id": e["public_id"], "type": e["type"], "status": e["status"], "attempts": e["attempts"],
            "last_code": e["last_code"], "last_error": e["last_error"],
            "created_at": e["created_at"].isoformat(),
            "delivered_at": e["delivered_at"].isoformat() if e["delivered_at"] else None}
