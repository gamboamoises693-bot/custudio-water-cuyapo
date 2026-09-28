"""
Customer model - Firestore collection: customers

Fields:
    id, name, barangay, phone, type (residential/store/office/bulk),
    chat_thread_id, total_orders, utang_amount, created_at,
    loyalty_gallons, loyalty_free_gallons (MODULE 9, see models/loyalty.py)

Pricing is NOT stored per-customer anymore - as of the official price list
update, every order prices itself from the shared catalog in
models/pricing.py (same price for every customer, whatever container type
they order), so there's no `price_per_container` field to set here.
"""

from datetime import datetime, timezone
from firebase_config import db, server_timestamp

COLLECTION = "customers"

VALID_TYPES = ("residential", "store", "office", "bulk")


def create_customer(name, barangay, phone, cust_type="residential"):
    """Creates a customer and its linked chat thread. Returns the new customer dict."""
    if cust_type not in VALID_TYPES:
        cust_type = "residential"

    customer_ref = db.collection(COLLECTION).document()
    customer_id = customer_ref.id

    # Import here (not at top) to avoid circular import between customers <-> chats
    from models.chats import create_thread_for_customer
    thread_id = create_thread_for_customer(customer_id, name)

    data = {
        "id": customer_id,
        "name": name.strip(),
        "barangay": barangay.strip(),
        "phone": phone.strip(),
        "type": cust_type,
        "chat_thread_id": thread_id,
        "total_orders": 0,
        "utang_amount": 0.0,
        "created_at": server_timestamp(),
    }
    customer_ref.set(data)
    return data


def get_customer(customer_id):
    snap = db.collection(COLLECTION).document(customer_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


def list_customers(barangay=None, search=None):
    """Returns all customers, optionally filtered by exact barangay and/or
    a case-insensitive substring match on name/phone (done in Python since
    Firestore doesn't support free-text search natively)."""
    query = db.collection(COLLECTION)
    if barangay:
        query = query.where("barangay", "==", barangay)

    docs = [d.to_dict() for d in query.stream()]

    if search:
        s = search.strip().lower()
        docs = [
            c for c in docs
            if s in c.get("name", "").lower() or s in c.get("phone", "").lower()
        ]

    docs.sort(key=lambda c: c.get("name", ""))
    return docs


def update_customer(customer_id, updates: dict):
    db.collection(COLLECTION).document(customer_id).update(updates)
    return get_customer(customer_id)


def delete_customer(customer_id):
    db.collection(COLLECTION).document(customer_id).delete()


def increment_total_orders(customer_id, by=1):
    customer = get_customer(customer_id)
    if not customer:
        return
    new_total = customer.get("total_orders", 0) + by
    update_customer(customer_id, {"total_orders": new_total})


def adjust_utang(customer_id, delta_amount):
    """delta_amount positive = adds to utang (customer now owes more),
    negative = reduces utang (customer paid off some/all)."""
    customer = get_customer(customer_id)
    if not customer:
        return
    new_utang = max(0.0, float(customer.get("utang_amount", 0.0)) + float(delta_amount))
    update_customer(customer_id, {"utang_amount": new_utang})


def list_inactive_customers(days=14):
    """Customers with no order in the last `days` days (or none ever)."""
    from models.orders import get_last_order_date_map

    all_customers = list_customers()
    last_order_map = get_last_order_date_map()
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

    inactive = []
    for c in all_customers:
        last_ts = last_order_map.get(c["id"])
        if last_ts is None or last_ts < cutoff:
            inactive.append(c)
    return inactive


def top_customers(limit=10):
    customers = list_customers()
    customers.sort(key=lambda c: c.get("total_orders", 0), reverse=True)
    return customers[:limit]
