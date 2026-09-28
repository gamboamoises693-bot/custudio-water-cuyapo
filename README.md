# CUSTODIO WATER REFILLING STATION - Cuyapo Branch

Water Station Management System: Customer Management, Personal Chat (Messenger-style,
NOT auto-SMS), Order & Delivery, Rider App, Inventory & Tank Monitor, Sales Dashboard,
Reports, and Chat Broadcast (still personal per-thread).

Built with **Flask + Firebase Firestore + Firebase Storage + Tailwind CSS**, ready to
push to GitHub and deploy to **Render.com**.

## Modules

1. Customer Management + Chat Profile
2. Personal Chat per Customer (Messenger-style, main feature)
3. Order & Delivery + Chat Integration (rider marks delivered → auto chat notification)
4. Inventory & Tank Monitor (low-stock / low-tank alerts)
5. Sales & Collection + Chat Payment (confirm GCash receipts sent in chat)
6. Rider Management (daily performance + basic "daya" / cheating detection)
7. Reports (Daily Closing, Weekly/Monthly per-barangay, Inactive Customers, Best Customers)
8. Chat Broadcast (sent as individual personal chat messages, never SMS)
9. **Customer Self-Service Portal** (`/customer`) - a customer logs into their
   OWN account (phone number + password, set by the owner via Customers > 🔑)
   and can place their own orders (picking from the official container
   catalog - see Pricing below), see order history + live status, and earn
   free gallons on a **loyalty card** (buy 100 gallons total, get 5 free
   gallons applied automatically to their next order - matches the branch's
   printed loyalty card exactly), plus spin a "Free Spin" bonus (extra
   gallons) once their order is delivered. Modeled after the owner's other
   app ("Omega Ice")'s reseller portal - see `models/customer_auth.py` and
   `models/loyalty.py` for the full design notes, including two deliberate
   adaptations from that app: forgot-password codes are shown on-screen
   instead of sent by SMS (no paid SMS gateway required), and a customer can
   never self-mark their own order "Delivered" (unlike Omega Ice) since
   Custodio Water's "Delivered" status also carries the payment info only
   the rider can honestly supply.

## Pricing

Every order - whether created by staff (Orders page or Chat), or by a
customer through the self-service portal - prices itself from ONE shared
catalog (`models/pricing.py`), the official walk-in/store price list:

| Container            | Price   | Counts as (loyalty card) |
|-----------------------|---------|---------------------------|
| 1 Gallon (Slim)       | ₱30.00  | 1 gallon |
| 1 Gallon (Round)      | ₱30.00  | 1 gallon |
| 6 Gallon              | ₱150.00 | 6 gallons |
| 6-10 Liters (Small Bottle) | ₱15.00 | ~2 gallons (approximation - see `models/pricing.py`) |

There's no more per-customer negotiated price - to change a price or add a
new container type, edit `CONTAINER_TYPES` in `models/pricing.py` (one
place, used everywhere).

## Tech stack

- **Backend:** Python 3.11, Flask (blueprints per module)
- **Database:** Firebase Firestore (`firebase-admin` SDK)
- **File storage:** Firebase Storage (GCash receipts, delivery photo proof)
- **Frontend:** Server-rendered Jinja2 templates + Tailwind CSS (CDN) + vanilla JS
- **Auth:** Simple session-based login with 3 roles (owner / staff / rider) - see note below
- **Hosting:** Render.com (`render.yaml` included)

> **Note on Auth:** the original spec asked for Firebase Auth. This build uses a small
> `users` Firestore collection + Flask sessions instead (see `auth.py`) - it's simpler to
> set up (zero extra Firebase Console configuration) and behaves identically from every
> route's point of view (`login_required` / `role_required` decorators only touch the
> session). Swapping in real Firebase Auth later only means rewriting `verify_login()`.

## Dev/Demo mode (no Firebase project needed yet)

This app works **immediately**, with zero Firebase setup, using a built-in mock Firestore
that stores data in `instance/mock_firestore.json`. This is real, tested, working code -
not a stub - so you can try every module (customers, chat, orders, delivery, inventory,
riders, reports) today and swap in real Firebase whenever your project/credentials are ready.

```bash
pip install -r requirements.txt
python seed.py     # creates 10 demo customers, 2 riders, 5 orders, tanks, inventory, owner login
python app.py       # -> http://localhost:5000
```

Login with the account `seed.py` prints (default: `owner@custudio.com` / `custudio123`).

## Going to production: connect real Firebase

1. **Create a Firebase project** at https://console.firebase.google.com
2. Enable **Firestore** (Native mode) and **Storage**.
3. Project Settings → Service Accounts → **Generate new private key** (downloads a JSON file).
4. Copy `.env.example` to `.env` and either:
   - paste the whole JSON file content as one line into `FIREBASE_CRED_JSON`, or
   - save the file as `serviceAccountKey.json` in this folder and set `FIREBASE_CRED_FILE=serviceAccountKey.json`
5. Set `FIREBASE_STORAGE_BUCKET` (shown in Firebase Console → Storage, e.g. `your-project.appspot.com`).
6. Set a strong `FLASK_SECRET_KEY`.
7. Run `python seed.py` again - it will now write to real Firestore instead of the mock file.
8. **Firestore security rules** - spec says "allow all for development"; before going live,
   at minimum restrict writes to authenticated requests only (this app's writes all go
   through the trusted Admin SDK server-side, so rules mainly matter if you ever add
   direct client-side Firestore access, e.g. real-time `onSnapshot` - see `static/js/chat.js`
   for notes on that upgrade path).

## Local setup from scratch

```bash
git clone <your-repo-url>
cd custudio-water-cuyapo
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # fill in Firebase creds when ready (optional for demo mode)
python seed.py
python app.py
```

## Deploy to Render

1. Push this repo to GitHub.
2. On Render.com → New → Blueprint → point to your repo (`render.yaml` is already configured).
3. Fill in the environment variables Render asks for: `FIREBASE_CRED_JSON`,
   `FIREBASE_STORAGE_BUCKET`, `OWNER_EMAIL`, `OWNER_PASSWORD`.
4. Deploy. Render runs `gunicorn app:app` automatically.
5. Run `python seed.py` once locally against your real Firebase project (or write a small
   one-off Render Shell job) to seed the initial owner login + demo data.

## Customer Portal setup

The Customer Portal itself needs zero extra setup - it works the moment you
deploy, using the same Firestore connection as everything else. Two things
are optional add-ons:

- **Giving a customer their login:** in the owner app, go to **Customers**,
  tap 🔑 next to their name, and set a password. Their phone number on file
  is their username. Tell them to log in at `/customer`.
- **Push notifications** (a phone notification even when the app/tab is
  closed - new order confirmations, new-device login alerts): optional,
  silently disabled until you set it up.
  1. `python generate_vapid_keys.py` (uses the `cryptography` package,
     already installed - no extra install needed)
  2. Copy the two printed values into Render > Environment as
     `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY`
  3. (Optional) also set `VAPID_CLAIMS_SUB=mailto:your-real-email@example.com`
  4. Redeploy - push notifications turn on automatically once both keys are
     present (see `push_notify.py`)

## Firestore collections

`customers` (loyalty gallons/free credits live on each customer doc as
`loyalty_gallons`/`loyalty_free_gallons` - no more `price_per_container`,
see Pricing above), `chat_threads`, `chat_messages`, `orders` (container
type/price/gallons recorded as `container_type`/`container_label`/
`unit_price`/`gallons_total`; loyalty discount as `free_gallons_applied`/
`loyalty_discount_amount`), `deliveries`, `tanks`, `inventory_items`,
`riders`, `expenses`, `users` (auth), `customer_otps`,
`customer_login_logs`, `customer_activity_logs`, `push_subscriptions`,
`settings` (loyalty program pause switch), `order_spins`.

## Project structure

```
custudio-water-cuyapo/
├── app.py                  # Flask app factory, login/logout, blueprint registration
├── auth.py                 # Session-based auth helpers (login_required, role_required)
├── firebase_config.py       # Firebase Admin init + local mock-Firestore dev fallback
├── seed.py                  # Demo data seeder
├── requirements.txt
├── .env.example
├── render.yaml
├── models/                  # Firestore collection access (CRUD + business logic)
│   ├── customers.py
│   ├── chats.py
│   ├── orders.py             # orders + deliveries
│   ├── pricing.py            # container/product catalog (the official price list)
│   ├── inventory.py          # tanks + inventory_items
│   └── riders.py
├── routes/                  # Flask blueprints (one per module)
│   ├── customers.py
│   ├── chats.py              # + broadcast
│   ├── orders.py
│   ├── deliveries.py          # rider app + mark-delivered
│   ├── inventory.py
│   ├── sales.py               # dashboard + reports
│   └── riders.py
├── templates/                # Tailwind/Jinja2 templates
└── static/
    ├── css/style.css
    └── js/ (chat.js, dashboard.js)
```

## Known MVP limitations / next steps

- Real-time chat uses 3-second polling, not a true Firestore `onSnapshot` listener (that
  requires exposing a public Firebase Web config + security rules - see the comment at the
  top of `static/js/chat.js` for the exact upgrade steps).
- No Google Maps embed yet for the delivery map (spec module 3 "bonus"); rider location isn't
  tracked. Add a `mcp`/Maps JS embed on `rider_app.html` once you have a Maps API key.
- "Walk-in" sales aren't a separate order type yet - the dashboard currently only counts
  delivered orders; add a `type: delivery|walkin` field on `orders` if you need that split.
- Image uploads fall back to local disk storage in dev/demo mode (no real Firebase Storage
  configured) - fine for testing, but set `FIREBASE_STORAGE_BUCKET` before production so
  photos survive redeploys.
