"""
Seed script - populates demo data so you can try the whole system immediately:
  - 1 owner login account
  - 10 customers around Cuyapo, Nueva Ecija (with chat threads)
  - 2 riders (1 with its own rider-app login)
  - 5 sample orders (mix of pending / on_delivery / delivered+paid)
  - 2 tanks (raw + purified) and a few inventory items
  - a couple of sample chat messages per customer

Run:
    python seed.py

Safe to re-run: it always creates fresh documents (IDs are auto-generated),
so running it twice will just double the demo data - delete
instance/mock_firestore.json (dev/demo mode) or clear your Firestore
collections if you want a clean slate before re-seeding.
"""

import os
import random
from dotenv import load_dotenv

load_dotenv()

import firebase_config  # noqa: F401 - initializes db before models are imported
from auth import create_user, get_user_by_email
from models.customers import create_customer, update_customer
from models.chats import send_message
from models.riders import create_rider
from models.orders import create_order, assign_rider, mark_delivered
from models.inventory import create_tank, create_item
from models.customer_auth import hash_password
from models.pricing import list_container_types

CUYAPO_BARANGAYS = [
    "District 1", "District 2", "District 3", "District 4",
    "Poblacion", "Sto. Domingo", "San Juan", "Villa Fronda",
]

CUSTOMER_NAMES = [
    "Aling Nena Santos", "Mang Rodel Cruz", "Josie Ramos", "Bong Fernandez",
    "Tita Baby Reyes", "Kuya Jun Dela Cruz", "Marites Bautista", "Store: Sari-Sari ni Aling Puring",
    "Office: Cuyapo Rural Health Unit", "Store: Cuyapo Mini Mart",
]


def main():
    owner_email = os.environ.get("OWNER_EMAIL", "owner@custudio.com")
    owner_password = os.environ.get("OWNER_PASSWORD", "custudio123")

    print("== Seeding CUSTODIO WATER REFILLING STATION - Cuyapo Branch demo data ==")

    if not get_user_by_email(owner_email):
        create_user(owner_email, owner_password, "Boss (Owner)", role="owner")
        print(f"[OK] Owner account created: {owner_email} / {owner_password}")
    else:
        print(f"[skip] Owner account already exists: {owner_email}")

    # --- Customers (pricing is catalog-based now, see models/pricing.py -
    # no per-customer price to set here anymore) ---
    customers = []
    for i, name in enumerate(CUSTOMER_NAMES):
        cust_type = "store" if "Store:" in name else ("office" if "Office:" in name else "residential")
        display_name = name.split(": ", 1)[-1]
        barangay = CUYAPO_BARANGAYS[i % len(CUYAPO_BARANGAYS)]
        customer = create_customer(display_name, barangay, f"09{random.randint(100000000, 999999999)}", cust_type)
        customers.append(customer)
        # a little sample chat history so the Messenger-style UI isn't empty
        send_message(customer["chat_thread_id"], "customer", customer["id"], "Hi po, pwede pa order ng tubig?")
        send_message(customer["chat_thread_id"], "owner", "system", "Opo, ilang containers po kailangan nyo?")
    print(f"[OK] Created {len(customers)} customers with chat threads.")

    # --- MODULE 9: Customer Portal demo login (first 3 customers get a
    # password so you can try /customer without setting one by hand first
    # via Customers > 🔑) ---
    DEMO_CUSTOMER_PASSWORD = "custudio123"
    for c in customers[:3]:
        update_customer(c["id"], {"password_hash": hash_password(DEMO_CUSTOMER_PASSWORD)})
    print(f"[OK] Customer Portal login enabled for: {', '.join(c['phone'] for c in customers[:3])} (password: {DEMO_CUSTOMER_PASSWORD})")

    # --- Riders ---
    rider1 = create_rider("Ricky Manalo", "09171234567", "TRC-001")
    rider2 = create_rider("Jefferson Ocampo", "09281234567", "TRC-002")
    if not get_user_by_email("rider1@custudio.com"):
        create_user("rider1@custudio.com", "rider123", rider1["name"], role="rider", rider_id=rider1["id"])
        print("[OK] Rider login created: rider1@custudio.com / rider123 (Ricky Manalo)")
    print(f"[OK] Created 2 riders: {rider1['name']}, {rider2['name']}")

    # --- Tanks + Inventory ---
    create_tank("raw", 80)
    create_tank("purified", 42)  # intentionally low, to demo the red alert banner
    create_item("empty_bottle", 120, 30)
    create_item("cap", 500, 100)
    create_item("seal", 40, 50)  # intentionally low stock
    create_item("plastic", 300, 50)
    print("[OK] Created tanks (raw 80%, purified 42% - triggers low alert) and inventory items.")

    # --- Orders (5 sample orders, mixed statuses + container types) ---
    o1 = create_order(customers[0]["id"], 5, delivery_date="2026-09-28", container_type="gallon_slim")
    o2 = create_order(customers[1]["id"], 3, delivery_date="2026-09-28", container_type="gallon_round")
    o3 = create_order(customers[2]["id"], 17, delivery_date="2026-09-27", container_type="six_gallon")
    o4 = create_order(customers[3]["id"], 2, delivery_date="2026-09-27", container_type="small_bottle")
    o5 = create_order(customers[4]["id"], 12, delivery_date="2026-09-27", container_type="six_gallon")

    # o3 and o5: assign rider + mark delivered (feeds dashboard sales + rider performance)
    assign_rider(o3["id"], rider1["id"])
    mark_delivered(o3["id"], rider1["id"], delivered_qty=17, payment_type="cash", amount_collected=o3["amount_due"])

    assign_rider(o5["id"], rider2["id"])
    mark_delivered(o5["id"], rider2["id"], delivered_qty=12, payment_type="gcash", amount_collected=o5["amount_due"])
    # (MODULE 9 side-effect: mark_delivered() above already added loyalty
    # gallons - customers[2] bought 17x six_gallon = 102 gallons, so they
    # now have 2 gallons progress + 5 FREE gallons credit (crossed the
    # 100-gallon mark); customers[4] bought 12x six_gallon = 72/100
    # gallons, no free credit yet. Both viewable at /customer once you set
    # their portal password.)

    # o4: delivered but on utang (unpaid) - demonstrates utang tracking
    assign_rider(o4["id"], rider1["id"])
    mark_delivered(o4["id"], rider1["id"], delivered_qty=2, payment_type="utang", amount_collected=0)

    # o1, o2 stay pending -> shows up in Orders page + dashboard pending count
    print(f"[OK] Created 5 sample orders across {len(list_container_types())} container types "
          f"(2 delivered+paid, 1 delivered+utang, 2 pending).")

    print("\n== DONE ==")
    print(f"Owner login: {owner_email} / {owner_password}")
    print(f"Customer Portal login (try /customer): {customers[0]['phone']} / {DEMO_CUSTOMER_PASSWORD}")
    print("Run: python app.py, then open http://localhost:5000")


if __name__ == "__main__":
    main()
