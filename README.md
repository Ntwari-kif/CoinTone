# PayVerify – multi-panel platform

Python (Flask) + SQLite database. Five separate pages, all reading the same live database
(each panel refreshes itself every few seconds, so changes show up everywhere at once).

| Page | URL | Who |
|---|---|---|
| Login | /login | everyone |
| Register | /register | shop owners and cashiers |
| Admin panel | /admin | you (the platform owner) |
| Owner panel | /owner | shop owners |
| Cashier panel | /cashier | cashiers approved by an owner |

## Run it
```
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000  (Windows: double-click `run.bat`; Mac/Linux: `./run.sh`)

First admin login (created automatically): **admin@payverify.local / admin123** – change it
with the "Password" button, or set `PV_ADMIN_EMAIL` / `PV_ADMIN_PASSWORD` before the first start.

## How it works
1. **Shop owner registers** → gets a 6-letter shop code. The shop is *locked* until the first payment.
2. **Owner pays 5000/month** by mobile money to your number, pastes the confirmation SMS in the Owner panel.
   The SMS is read automatically (amount + transaction code; each code can be used once).
   Paying 10000 buys 2 months, and so on.
3. **You (admin) confirm** the payment in the Admin panel → the shop gets 30 days per 5000.
   You can also suspend a shop or add days manually. Expired/unpaid shops are locked (owner + cashiers).
4. **Cashier registers** with the shop code → status *pending*. They **cannot sign in** until the owner
   presses **Allow** in "Cashier access". The owner can **Remove** them at any time (they are kicked out immediately).
5. **Cashier verifies payments** (paste SMS / manual cash, card, bank). Results appear instantly in the
   owner's dashboard with fraud alerts (duplicate code, short payment, old SMS, manual entries…).

## Settings (config.py or environment variables)
| Variable | Default | Meaning |
|---|---|---|
| PV_FEE | 5000 | monthly subscription |
| PV_CURRENCY | KES | KES, TZS, UGX, GHS, RWF… |
| PV_PAY_NUMBER / PV_PAY_NAME | placeholder | **put your real number here** – shown to owners |
| PV_ADMIN_EMAIL / PV_ADMIN_PASSWORD | see above | first admin |
| PV_DB | instance/payverify.db | database file |

## Files
- `app.py` – server + all API routes · `db.py` – database tables · `sms.py` – SMS reader
- `templates/` – the 5 pages · `static/` – shared style and script
- Database file: `instance/payverify.db` (back this file up!)

## Going live
- Run behind HTTPS (e.g. a VPS with Caddy/Nginx). `python app.py` already uses the production server (waitress).
- SQLite handles many shops comfortably. For very large scale, swap `db.py` to PostgreSQL – the SQL is standard.
- Subscription payments are confirmed by you (admin) – no payment-gateway keys are needed.

## Light / dark mode
Every page has a 🌙 / ☀️ button (top bar, or top-right on login/register). The choice is saved on the device;
until you choose, the page follows your phone's system setting. Colours live at the top of `static/style.css`.
