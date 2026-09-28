"""
One-off maintenance script (run this ONCE, locally, the same way you ran seed.py):

  1. Deletes ALL demo/seed data (customers, chat threads/messages, orders,
     deliveries, riders, tanks, inventory items) from the REAL Firestore -
     keeps the `users` collection (login accounts) intact, so you keep your
     owner login.
  2. Optionally changes the owner account's password to a new one you type in.

Run:
    python reset_and_secure.py

Safe: only touches the Firestore collections listed below (your real, live
database - same one your Render app uses). Does not touch your code or your
GitHub repo, and does not affect other login accounts unless you target them.
"""

import os


def _load_env_file(path=".env"):
    """Minimal .env loader (no external dependency needed) - reads KEY=VALUE
    lines from the given file and sets them into os.environ if not already
    set. Used instead of python-dotenv here since installing packages via pip
    has been unreliable in some mobile Python environments (e.g. Pydroid)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()

import firebase_config  # noqa: F401 - initializes db before use
from firebase_config import db, USING_MOCK_DB
from werkzeug.security import generate_password_hash

# Demo data collections created by seed.py. NOTE: "users" (login accounts) is
# intentionally NOT in this list - we never want to accidentally wipe logins.
DEMO_COLLECTIONS = [
    "customers",
    "chat_threads",
    "chat_messages",
    "orders",
    "deliveries",
    "tanks",
    "inventory_items",
]


def wipe_demo_data():
    if USING_MOCK_DB:
        print("[!] Naka-MOCK mode ka ngayon (walang FIREBASE_CRED_FILE/JSON na na-detect).")
        print("    Walang gagalawin sa totoong Firebase - i-check muna ang .env mo.")
        return

    total_deleted = 0
    for name in DEMO_COLLECTIONS:
        docs = list(db.collection(name).stream())
        for d in docs:
            d.reference.delete()
        print(f"[OK] {name}: {len(docs)} document/s deleted.")
        total_deleted += len(docs)
    print(f"\n[DONE] Total {total_deleted} demo document/s deleted mula sa totoong Firestore.")


def change_owner_password():
    if USING_MOCK_DB:
        print("[!] Naka-MOCK mode ka - hindi natin gagalawin ang totoong login dito.")
        return

    email = input("Owner email (Enter lang para sa owner@custudio.com): ").strip() or "owner@custudio.com"
    email = email.lower()

    user_doc = None
    for d in db.collection("users").stream():
        data = d.to_dict()
        if data.get("email", "").lower() == email:
            user_doc = d
            break

    if not user_doc:
        print(f"[!] Walang account na may email na {email}. Na-skip ang password change.")
        return

    new_password = input("Bagong password (min. 6 characters, makikita habang tine-type): ").strip()
    if len(new_password) < 6:
        print("[!] Masyadong maikli ang password (kailangan min. 6 characters). Hindi na-save.")
        return

    user_doc.reference.update({"password_hash": generate_password_hash(new_password)})
    print(f"[OK] Napalitan na ang password ni {email}. Gamitin mo na ang bagong password sa susunod na login.")


if __name__ == "__main__":
    print("== CUSTODIO WATER REFILLING STATION - Reset demo data + secure owner account ==\n")

    confirm = input("I-DELETE ang lahat ng demo customers/orders/riders/tanks/inventory? (yes/no): ").strip().lower()
    if confirm == "yes":
        wipe_demo_data()
    else:
        print("[skip] Hindi tinanggal ang demo data.")

    print()
    change_pw = input("Palitan ang owner password ngayon? (yes/no): ").strip().lower()
    if change_pw == "yes":
        change_owner_password()

    print("\n== TAPOS NA ==")
