# HIJASI

**Closed-loop wallet API for businesses in countries that Stripe and PayPal don't serve.**

A merchant (a farm, a game studio, a canteen) integrates our REST API. Their customers top up a
wallet through the merchant's own payment gateway, and orders are paid from that balance. We
provide the ledger, limits, refunds, idempotency and merchant vetting. The merchant keeps the money.

Each wallet can only be spent at the merchant that issued it, with no cash-out. This matches the
"closed system" prepaid instrument model in India's RBI rules, which does not need RBI
authorisation (the same model as BigBasket's wallet).

Team: Siam, Hieu, Jason — Oslo sprint hackathon 2026.

## What's in the repo

| Path | What it is |
|---|---|
| `backend/` | Python Flask API + MySQL ledger (port 5000) |
| `backend/schema.sql` | Database tables |
| `frontend/` | Landing page, merchant dashboard, admin panel, API docs (plain HTML/CSS/JS) |
| `demo-farm/` | **Green Valley Farm**, a separate demo shop that integrates over the API (port 5001) |

## Run it

**1. MySQL + phpMyAdmin**

Use XAMPP/MAMP, a local MySQL, or Docker:

```bash
docker compose up -d        # MySQL on 3306, phpMyAdmin on http://localhost:8080 (root / rootpw)
```

**2. Backend**

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # set DB_PASSWORD, ADMIN_TOKEN (and Stripe keys if you want)
python seed.py eur          # creates tables + approved demo merchant, writes demo-farm/.env
python app.py               # http://localhost:5000
```

**3. Demo farm**

```bash
cd demo-farm
pip install -r requirements.txt
python app.py               # http://localhost:5001
```

## Demo script

1. **Farm shop** — http://localhost:5001: log in with any name + phone, top up 50.00, pay, buy a veg box.
2. **Merchant dashboard** — http://localhost:5000/merchant, log in as `farm@demo.hijasi` / `farmdemo123`:
   wallet balances, ledger, API key, top-up limits.
3. **New merchant** — apply at `/merchant#register` → it waits for review.
4. **Admin** — http://localhost:5000/admin with your `ADMIN_TOKEN`: approve/reject, risk-word flags
   (crypto, gambling, forex, …).
5. **API docs** — http://localhost:5000/docs

## Payments

- **No Stripe keys** → built-in mock gateway (a "Pay now" page). Works offline.
- **Stripe test mode** → set `STRIPE_SECRET_KEY=sk_test_…` in `backend/.env`. Pay with card
  `4242 4242 4242 4242`, any future date, any CVC. The wallet is credited when the customer comes
  back from Checkout (we verify the session with Stripe). Optionally also use the webhook:
  `stripe listen --forward-to localhost:5000/webhooks/stripe` and put the `whsec_…` into
  `STRIPE_WEBHOOK_SECRET`.

Stripe is only the demo adapter. In production a merchant plugs in the local gateway they already
use (Easebuzz, Razorpay, Paystack…) — see `backend/payments.py`.

## How money stays correct

- Money is integers in minor units, never floats.
- `ledger_entries` is append-only, with `balance_after` on every row.
- Charges lock the wallet row (`SELECT … FOR UPDATE`), so parallel orders can't overspend.
- `Idempotency-Key` on charges/refunds makes retries safe.
- Top-ups are credited exactly once, only after the gateway confirms.
- Refunds go back to the wallet and can never exceed the original charge.
- Secret API keys are stored only as SHA-256 hashes.
