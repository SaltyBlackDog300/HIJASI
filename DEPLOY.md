# Deploying Kassara to cPanel (A2 Hosting / hosting.com)

Target: **https://kassara.org** (API, dashboards, checkout) and **https://demo.kassara.org** (demo farm shop).
Everything runs as cPanel **Setup Python App** (Passenger). The code sits in `/home/jasoslgv/kassara.org/`;
`backend/` and `demo-farm/` each contain an `.htaccess` that blocks all web access, so `.env` secrets can't be
downloaded. Calcunator and other sites are untouched.

```
/home/jasoslgv/kassara.org/
├── backend/      ← Python app #1 → kassara.org        (.env + .htaccess deny)
├── demo-farm/    ← Python app #2 → demo.kassara.org   (.env + .htaccess deny)
└── frontend/     ← pages, served by the backend
```

## 0. Check the hosting first (2 minutes)

In cPanel, confirm:
- **Software → Setup Python App** exists, and offers **Python 3.10 or newer** (3.11/3.12 preferred).
  The verification module needs 3.10+.
- **Advanced → Terminal** exists (or SSH access). Needed once, to seed the database.
- https://kassara.org opens with a valid padlock (DNS has propagated and SSL is installed).
  If not, wait for propagation, then run **Security → SSL/TLS Status → Run AutoSSL**.

## 1. Get the code onto the server

**Files → Git Version Control → Create**
- Clone a Repository: **on**
- Clone URL: `https://github.com/SaltyBlackDog300/HIJASI.git`
- Repository Path: `/home/jasoslgv/kassara.org` (or upload the files instead)
- Repository Name: `kassara`

Later updates: open the repository in Git Version Control → **Pull or Deploy → Update from Remote**,
then restart the Python apps (step 7).

## 2. Create the database

**Databases → MySQL Databases**
1. Create database: `kassara` → becomes `jasoslgv_kassara`
2. Create user: `kassara` → `jasoslgv_kassara`, with a strong generated password (save it)
3. Add user to database → **ALL PRIVILEGES**

## 3. Create the secrets file (outside the web root)

**Files → File Manager** → `kassara.org/backend/` → **+ File** → `.env` → Edit:

```
PRODUCTION=true
PUBLIC_URL=https://kassara.org

DB_HOST=localhost
DB_PORT=3306
DB_USER=jasoslgv_kassara
DB_PASSWORD=<the database password>
DB_NAME=jasoslgv_kassara

FLASK_SECRET_KEY=<64 random characters>
ADMIN_TOKEN=<a long random admin password>

STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...       (step 8; leave empty until then)
DEMO_SHOW_CODES=true

DEMO_SHOP_URL=https://demo.kassara.org
DEMO_FARM_URL=https://demo.kassara.org
DEMO_FARM_API_URL=https://kassara.org

SMTP_HOST=mail.kassara.org
SMTP_PORT=465
SMTP_USER=no-reply@kassara.org
SMTP_PASSWORD=<the mailbox password>
MAIL_FROM=no-reply@kassara.org
```

Then right-click `.env` → **Change Permissions** → `600`. Never commit this file (it is in `.gitignore`).

## 4. Create the Kassara Python app

First back up the current `kassara.org/.htaccess` (File Manager → right-click → Copy → `.htaccess.bak`).

**Software → Setup Python App → Create Application**
- Python version: highest 3.x available (≥ 3.10)
- Application root: `kassara.org/backend`
- Application URL: `kassara.org` (leave the path empty)
- Application startup file: `passenger_wsgi.py`
- Application Entry point: `application`
- Create

On the app's page: **Configuration files** → add `requirements.txt` → **Run Pip Install**.

## 5. Create tables and the demo merchant (once)

Copy the "Enter to the virtual environment" command shown at the top of the app's page, then in
**Terminal**:

```bash
source /home/jasoslgv/virtualenv/kassara.org/backend/3.11/bin/activate   # paste YOUR command
cd ~/kassara.org/backend
python seed.py eur
```

This creates all tables, the approved demo merchant `farm@demo.kassara.org` / `farmdemo123`, and writes
the demo shop's API key into `kassara.org/demo-farm/.env`. It is safe to run again: it re-issues the
demo shop's key (restart the demo app afterwards).

## 6. Demo shop on demo.kassara.org

1. **Domains → Create A New Domain**: `demo.kassara.org` (uncheck "share document root").
2. **SSL/TLS Status → Run AutoSSL** so it gets a certificate.
3. **Setup Python App → Create Application**: root `kassara.org/demo-farm`, URL `demo.kassara.org`,
   startup file `passenger_wsgi.py`, entry point `application` → then add `requirements.txt` →
   **Run Pip Install**.

## 7. Force HTTPS and restart

Edit `kassara.org/.htaccess` (and the demo subdomain's) and add **above** the cPanel/Passenger block:

```apache
RewriteEngine On
RewriteCond %{HTTPS} off
RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
```

Restart each app with **Restart** on its Setup Python App page. Open:
- https://kassara.org, https://kassara.org/merchant, https://kassara.org/admin (your `ADMIN_TOKEN`)
- https://demo.kassara.org

## 8. Stripe webhook (now possible because the site is public)

Stripe Dashboard (test mode) → **Developers → Webhooks → Add endpoint**
- URL: `https://kassara.org/webhooks/stripe`
- Event: `checkout.session.completed`

Copy the signing secret (`whsec_…`) into `STRIPE_WEBHOOK_SECRET` in `.env`, then restart the app.
Top-ups are also confirmed with Stripe directly when the customer returns, so the webhook is a
safety net for customers who close the tab.

## 9. Email (verification links, receipts, checkout codes)

**Email → Email Accounts → Create**: `no-reply@kassara.org` with a strong password. Put it in the
`SMTP_*` lines of `.env` (step 3) and restart. Kassara then sends:
- merchants: a confirm-your-email link at signup (needed before submitting the application and before
  creating API keys), and an email when the application is approved, rejected, suspended or needs info;
- customers (when the shop passes `email` to `POST /v1/wallets`): top-up, payment and refund receipts,
  and the one-time checkout code.

With `SMTP_HOST` empty, emails are written to `stderr.log` instead. Merchant accounts that existed before
this update are marked as verified automatically. For better inbox delivery, make sure
**Email → Email Deliverability** shows SPF and DKIM as valid for kassara.org.

## 10. Pricing plans

Plans live in `backend/pricing.py` (Starter €0 + 3% top-up fee min. €0.50, Business €29, Business Pro €79).
Merchants switch plans under **Plan & billing** in the dashboard: upgrades apply immediately, downgrades
on the 1st of next month. Top-up volume allowances for Business / Business Pro are **not decided yet**;
they show as "To be confirmed" and volume is tracked. Once decided, add to `.env` and restart, e.g.

```
PLAN_BUSINESS_TOPUP_ALLOWANCE=2500000      # minor units per month (25,000.00 EUR)
PLAN_BUSINESS_VOLUME_FEE_BPS=150           # 1.5% above the allowance; leave out to refuse instead
PLAN_BUSINESS_PRO_TOPUP_ALLOWANCE=...
PLAN_BUSINESS_PRO_VOLUME_FEE_BPS=...
KASSARA_MIN_FEE_INR=4500                   # minimum Starter fee for non-EUR merchants (optional)
```

These are proposed launch prices in test mode; subscriptions are tracked and estimated, not charged.

## Shared-hosting limitations

- **No background workers or long-running processes.** Passenger starts the app on the first request
  and stops it when idle, so the first visit after a quiet period takes a few seconds. Kassara needs no
  background jobs: payments are confirmed on return and by webhook.
- **Do not run `python app.py` on the server.** Passenger runs the app; `app.py` is for local development.
- **Resource limits** (CPU, memory, processes) apply per account and are shared with Calcunator.
  Fine for a demo; a VPS is the next step for real traffic.
- **Logs:** `kassara.org/backend/stderr.log` (or the app's log in cPanel) if you see a 500 error.
- **Code changes** need Git pull + Restart; editing files alone doesn't reload the app.
