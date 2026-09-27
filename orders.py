"""
Order & Delivery model - Firestore collections: orders, deliveries

orders: id, customer_id, containers_qty, order_date, delivery_date,
        status (pending/on_delivery/delivered/paid/utang), rider_id,
        chat_thread_id
deliveries: id, order_id, rider_id, delivered_qty, payment_type
            (cash/gcash/utang), amount_collected, photo_proof_url,
            delivered_at
"""

from datetime import datetime, timezone
from firebase_config import db, server_timestamp

ORDERS = "orders"
DELIVERIES = "deliveries"

VALID_STATUSES = ("pending", "on_delivery", "delivered", "paid", "utang")
VALID_PAYMENT_TYPES = ("cash", "gcash", "utang")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value):
    """Best-effort parse of a Firestore/mock timestamp into a float epoch seconds."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    # firestore.SERVER_TIMESTAMP sentinel or DatetimeWithNanoseconds
    if hasattr(value, "timestamp"):
        try:
            return value.timestamp()
        except Exception:
            return None
    return None


def create_order(customer_id, containers_qty, delivery_date=None, chat_thread_id=None, rider_id=None):
    from models.customers import get_customer, increment_total_orders

    customer = get_customer(customer_id)
    if not customer:
        raise ValueError("Customer not found")

    order_ref = db.collection(ORDERS).document()
    order_id = order_ref.id
    amount_due = float(containers_qty) * float(customer.get("price_per_container", 0))

    data = {
        "id": order_id,
        "customer_id": customer_id,
        "customer_name": customer.get("name"),
        "containers_qty": int(containers_qty),
        "amount_due": amount_due,
        "order_date": server_timestamp(),
        "delivery_date": delivery_date or "",
        "status": "pending",
        "rider_id": rider_id,
        "chat_thread_id": chat_thread_id or customer.get("chat_thread_id"),
    }
    order_ref.set(data)
    increment_total_orders(customer_id)
    return data


def create_order_from_chat(thread_id, containers_qty, delivery_date=None):
    """MODULE 3 flow: staff clicks 'Create Order from Chat'."""
    from models.chats import get_thread, send_message

    thread = get_thread(thread_id)
    if not thread:
        raise ValueError("Chat thread not found")

    order = create_order(
        customer_id=thread["customer_id"],
        containers_qty=containers_qty,
        delivery_date=delivery_date,
        chat_thread_id=thread_id,
    )
    send_message(
        thread_id, "owner", "system",
        f"✅ Order confirmed: {containers_qty} container/s"
        + (f" para sa {delivery_date}" if delivery_date else "") + ". Salamat po!",
    )
    return order


def get_order(order_id):
    snap = db.collection(ORDERS).document(order_id).get()
    return snap.to_dict() if snap.exists else None


def list_orders(status=None, rider_id=None):
    query = db.collection(ORDERS)
    if status:
        query = query.where("status", "==", status)
    if rider_id:
        query = query.where("rider_id", "==", rider_id)
    docs = [d.to_dict() for d in query.stream()]
    docs.sort(key=lambda o: _parse_ts(o.get("order_date")) or 0, reverse=True)
    return docs


def assign_rider(order_id, rider_id):
    db.collection(ORDERS).document(order_id).update({"rider_id": rider_id, "status": "on_delivery"})
    return get_order(order_id)


def mark_delivered(order_id, rider_id, delivered_qty, payment_type, amount_collected, photo_proof_url=None):
    """MODULE 3: rider marks delivered + uploads proof -> auto chat notification.
    MODULE 6: also updates rider's daily performance counters.
    MODULE 5: also feeds into daily sales totals (computed on read in routes/sales.py).
    """
    from models.chats import send_message
    from models.customers import adjust_utang
    from models.riders import bump_rider_daily_stats

    order = get_order(order_id)
    if not order:
        raise ValueError("Order not found")

    delivery_ref = db.collection(DELIVERIES).document()
    delivery_id = delivery_ref.id
    now = server_timestamp()

    delivery_ref.set({
        "id": delivery_id,
        "order_id": order_id,
        "customer_id": order["customer_id"],
        "rider_id": rider_id,
        "delivered_qty": int(delivered_qty),
        "payment_type": payment_type,
        "amount_collected": float(amount_collected),
        "photo_proof_url": photo_proof_url,
        "delivered_at": now,
    })

    new_status = "utang" if payment_type == "utang" else "paid"
    db.collection(ORDERS).document(order_id).update({
        "status": new_status,
        "delivered_qty": int(delivered_qty),
    })

    if payment_type == "utang":
        adjust_utang(order["customer_id"], order.get("amount_due", 0))
    elif order.get("status") == "utang":
        adjust_utang(order["customer_id"], -order.get("amount_due", 0))

    bump_rider_daily_stats(rider_id, delivered_qty, amount_collected)

    thread_id = order.get("chat_thread_id")
    if thread_id:
        text = f"Na-deliver na po ang {delivered_qty} container/s nyo. Salamat sa pag-order!"
        send_message(thread_id, "rider", rider_id, text, message_type="text")
        if photo_proof_url:
            send_message(thread_id, "rider", rider_id, message_type="image", image_url=photo_proof_url)

    return get_order(order_id)


def confirm_chat_payment(order_id, thread_id):
    """MODULE 5: staff clicks 'Confirm Payment' after customer sends GCash receipt in chat."""
    from models.chats import send_message
    from models.customers import adjust_utang

    order = get_order(order_id)
    if not order:
        raise ValueError("Order not found")

    db.collection(ORDERS).document(order_id).update({"status": "paid"})
    if order.get("status") == "utang":
        adjust_utang(order["customer_id"], -order.get("amount_due", 0))

    send_message(thread_id, "owner", "system", "✅ Confirmed na po ang payment nyo. Salamat!")
    return get_order(order_id)


def get_last_order_date_map():
    """customer_id -> most recent order timestamp (epoch seconds). Used for inactive-customer report."""
    orders = list_orders()
    result = {}
    for o in orders:
        cid = o.get("customer_id")
        ts = _parse_ts(o.get("order_date"))
        if ts is None:
            continue
        if cid not in result or ts > result[cid]:
            result[cid] = ts
    return result


def list_deliveries_for_date(date_str):
    """date_str format: YYYY-MM-DD. Filters deliveries whose delivered_at falls on that date."""
    docs = [d.to_dict() for d in db.collection(DELIVERIES).stream()]
    out = []
    for d in docs:
        ts = _parse_ts(d.get("delivered_at"))
        if ts is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        if day == date_str:
            out.append(d)
    return out


def list_all_deliveries():
    return [d.to_dict() for d in db.collection(DELIVERIES).stream()]


# Public alias - safe for other modules (e.g. routes/sales.py) to use directly
# instead of reaching into the "private" _parse_ts helper.
parse_ts = _parse_ts
