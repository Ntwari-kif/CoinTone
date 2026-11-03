# CTone – multi-panel platform

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

---
# Deploy on Render (from a phone)

Cost: Starter web service (about $7/month) + 1 GB disk (about $0.25/month). The FREE plan cannot keep a disk,
so it would lose all data – do not use it.

1. **GitHub**: create a free account at github.com → New repository → name `payverify`, **Private** → "uploading an existing file".
   Select ALL the files from this folder at once (they are all in one flat folder, no sub-folders) → Commit.
2. **Render**: render.com → sign up with GitHub → **New → Blueprint** → pick the `payverify` repo.
3. Fill in the 4 boxes: your payment number, your name, your admin email, a strong admin password. Press **Apply**.
4. Wait for "Live", open the `https://….onrender.com` address, and sign in with the admin email/password you typed.

Update later: edit/upload files on GitHub – Render redeploys automatically. Your data stays on the disk.
Backup: Render dashboard → your service → Disk → snapshots (or download `/var/data/payverify.db`).

## Notes (v3)
- Pages use `{{ url_for('static', filename='...') }}` links. The same files work flat (all in one folder) or with `templates/` + `static/` folders.
- On Render the admin panel shows a red banner if the database is NOT on a persistent disk (data would be erased on restart).

## Install as an app (v4)
CTone is now a Progressive Web App with your logo as the icon.
- **Android (Chrome):** open your site → tap the **📲 Install app** button on the sign-in page (or the ⬇️ in the top bar). If you don't see it: Chrome menu ⋮ → **Install app** / **Add to Home screen**.
- **iPhone (Safari):** tap Share → **Add to Home Screen**.
- Needs HTTPS (Render gives you that). It opens full-screen like a normal app, and shows a friendly "You're offline" screen when there's no internet.
- New files: `sw.js` (service worker), `pwa.js` (install button), `offline.html`, and 3 icons (`icon-192.png`, `icon-512.png`, `icon-maskable.png`).
- If you ever change the CSS/JS and phones keep the old version, change `V = "ctone-v1"` to `"ctone-v2"` in `sw.js`.

---
# Supabase database (v5) – replaces the Render disk
Your data now lives in Supabase (PostgreSQL), outside Render, so restarts/sleep/redeploys can never erase it.

1. **supabase.com** → New project. Choose a region near your shops and a database password with **letters and numbers only**. Save it.
2. Top bar → **Connect** → choose **Session pooler** → copy the URI. It looks like
   `postgresql://postgres.abcdef:[YOUR-PASSWORD]@aws-1-xx-region.pooler.supabase.com:5432/postgres`
   Replace `[YOUR-PASSWORD]` with your password (remove the brackets). Use *Session pooler* – the "Direct" string is IPv6-only and fails on Render.
3. **Render** → your service → **Environment** → add:
   - `DATABASE_URL` = the string from step 2  (never put this in GitHub or in chat)
   - `PV_TZ` = your timezone, e.g. `Africa/Nairobi`, `Africa/Dar_es_Salaam`, `Africa/Kampala`
4. Save → Render redeploys. The tables are created automatically on first start.
5. Sign in as admin: the panel shows **🟢 Database: Supabase (PostgreSQL)**. In Supabase → Table Editor you can see all shops, users and payments.

Notes
- The tables have Row Level Security turned on with no public policies, so Supabase's public API cannot read them; only this server can.
- Free Render services sleep after ~15 min idle (first open takes ~1 min). Use the Starter plan to keep it awake. The old disk is no longer needed.
- Backups: Supabase dashboard → Database → Backups. Optional: change `PV_TZ` so "today" and SMS times match your local clock.
- Testing on your own computer without Supabase: just don't set DATABASE_URL (a local SQLite file is used).


---
# v6 – Admin control centre, SMS listener, automatic activation
Open **/admin**. Tabs: Overview · Health · Connections · SMS listener · Settings · Users · Logs (all phone/tablet/desktop friendly).

## 1. Your payment number
Default is **0792032617** (change anytime: Admin → Settings → Billing). Set the correct **currency** there too (UGX, KES, TZS, RWF…) –
auto-activation only accepts SMS in that currency.

## 2. SMS listener (this is what makes activation automatic)
A website cannot read SMS. Put an "SMS forwarder / SMS to webhook" app on the phone that receives the money, then in Admin → SMS listener copy:
URL `https://YOUR-SITE/api/sms/ingest` and the secret token. Send JSON like `{"from":"{{sender}}","message":"{{message}}","token":"…"}`
(the token may also be sent as header `X-Token`). Use the **Test an SMS** box to try messages.

**How "genuine" is decided** (per network – MTN MoMo, Airtel Money, M-Pesa, Telecel, Orange): sender ID is the network's (not a normal phone number),
wording matches a known format, transaction-code format is valid, it is an incoming payment, and it has no scam wording (links, PIN/refund requests).
If a real message is marked suspicious, add its pattern in SMS listener → Extra formats.

**Activation without you:** the owner pays and submits the SMS text or just the transaction code. The server matches that code with a genuine
SMS received on YOUR phone through the listener (either order works). Match + amount ≥ fee + right currency = shop activated for the months paid.
Each SMS/code can be used once. Text pasted by an owner is never proof by itself (anyone can type one) unless you switch on "trust pasted SMS".

## 3. MTN / Airtel prompts (optional, instant)
Admin → Connections → paste your MTN MoMo Collections keys and/or Airtel Open API keys → Test. Owners then see "Send payment prompt":
they approve with their PIN and the shop activates. The server always re-asks the provider before activating (it never trusts a callback body).
These need accounts approved by MTN / Airtel – the code could not be tested against live accounts here.

## 4. Databases & other apps
Connections also holds Supabase and Firebase details and any other app (name + URL + key + health path), all encrypted in the database.
The app's own data runs on `DATABASE_URL` (Supabase) – the Database card lets you test a connection string before putting it in Render.
Optional env: `PV_VAULT_KEY` (encryption key for saved credentials; default derives from the app secret).

## 5. Also new
System health score · maintenance mode · owner announcement banner · disable/enable users and reset passwords · activity log · JSON backup.

---
# v7 – additions
- **Teach by example (SMS formats):** Admin → SMS listener → *Teach CTone an SMS format* (works for every shop and for subscription payments). Owners also have *Settings → Teach CTone your SMS format*, which applies only to that shop's cashiers. Paste ONE real SMS; the amount, code, name, phone, dates and balances become variables.
- **Forgot password:** login page → *Forgot password?* sends a request to Admin → Users (top). The admin checks the person, taps *Issue temporary password*, and sends it (e.g. WhatsApp). **Lost the admin password?** Set the Render variable `PV_ADMIN_RESET` to a new password, redeploy, log in, then delete the variable.
- **Overview:** 6-month income chart, expiring/lapsed shops with one-tap WhatsApp reminders, CSV export of payments.
- **Settings:** free trial days for new shops (default 0), `/healthz` uptime URL for a free monitor (UptimeRobot) which also keeps free hosting awake.
- **Logs:** unexpected server errors are recorded and counted in Health.
- New light/dark icons (sun/moon).
