"""
Order & Delivery model - Firestore collections: orders, deliveries

orders: id, customer_id, container_type, container_label, unit_price,
        containers_qty, gallons_total, order_date, delivery_date,
        status (pending/accepted/on_delivery/delivered/paid/utang),
        chat_thread_id

Order lifecycle (no more named/tracked riders in-system - the branch uses
its own external delivery riders, so the app only records the STATUS of a
delivery, not WHO drove it):
  pending    -> order just placed (by customer portal, chat, or staff)
  accepted   -> owner/staff confirms the order (accept_order())
  on_delivery -> owner/staff sends it out for delivery (start_delivery())
  delivered/paid/utang -> staff records the completed delivery + payment
                          (mark_delivered())
This exact sequence is also what the Customer Portal's order-tracking
stepper shows (see templates/customer_history.html).

deliveries: id, order_id, delivered_qty, payment_type (cash/gcash/utang),
            amount_collected, photo_proof_url, delivered_by (name of the
            staff/owner who processed the handover - free text, not a FK to
            a riders collection), delivered_at

Pricing comes from the container/product catalog in models/pricing.py (the
official walk-in/store price list), NOT from a per-customer field anymore.
"""

from datetime import datetime, timezone
from firebase_config import db, server_timestamp
from models.pricing import get_container_type, DEFAULT_CONTAINER_TYPE

ORDERS = "orders"
DELIVERIES = "deliveries"

VALID_STATUSES = ("pending", "accepted", "on_delivery", "delivered", "paid", "utang", "declined")
VALID_PAYMENT_TYPES = ("cash", "gcash", "utang")

# Walk-in orders (no linked customer account) - for buyers who don't want to
# register/join the loyalty promo. Sentinel customer_id used instead of a
# real Firestore customer doc id; every function below treats it as "no
# customer" (no loyalty gallons, no utang tracking, no chat thread).
WALKIN_CUSTOMER_ID = "walkin"
WALKIN_CUSTOMER_NAME = "Walk-in (No Account)"


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


def create_order(customer_id, containers_qty, delivery_date=None, chat_thread_id=None,
                  container_type=None):
    """`container_type` must be a key from models/pricing.py's catalog
    (defaults to DEFAULT_CONTAINER_TYPE if not given/invalid - callers
    should always pass a real one, this default only exists so old code
    that hasn't been updated yet doesn't hard-crash).

    Always prices at full catalog price - MODULE 9's loyalty free-gallon
    discount (see models/loyalty.py apply_free_gallons) is applied as a
    separate update AFTER this returns, from routes/customer_portal.py,
    since the discount is computed from this order's own gallons/amount."""
    from models.customers import get_customer, increment_total_orders

    if customer_id == WALKIN_CUSTOMER_ID:
        # No account, no loyalty program, no chat thread - per owner's
        # explicit request, for buyers who just want to pay and go.
        customer_name = WALKIN_CUSTOMER_NAME
        resolved_thread_id = chat_thread_id
    else:
        customer = get_customer(customer_id)
        if not customer:
            raise ValueError("Customer not found")
        customer_name = customer.get("name")
        resolved_thread_id = chat_thread_id or customer.get("chat_thread_id")

    catalog_entry = get_container_type(container_type) or get_container_type(DEFAULT_CONTAINER_TYPE)
    unit_price = float(catalog_entry["price"])
    gallons_per_unit = float(catalog_entry["gallons"])

    order_ref = db.collection(ORDERS).document()
    order_id = order_ref.id
    amount_due = float(containers_qty) * unit_price

    data = {
        "id": order_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "container_type": container_type if get_container_type(container_type) else DEFAULT_CONTAINER_TYPE,
        "container_label": catalog_entry["label"],
        "unit_price": unit_price,
        "containers_qty": int(containers_qty),
        "gallons_total": gallons_per_unit * int(containers_qty),
        "amount_due": amount_due,
        "free_gallons_applied": 0,
        "loyalty_discount_amount": 0,
        "order_date": server_timestamp(),
        "delivery_date": delivery_date or "",
        "status": "pending",
        "chat_thread_id": resolved_thread_id,
        "decline_reason": None,
        # Only the CUSTOMER's own confirm_delivery() (below) flips this -
        # it's what now triggers loyalty gallons, not mark_delivered().
        "customer_confirmed": False,
    }
    order_ref.set(data)
    if customer_id != WALKIN_CUSTOMER_ID:
        increment_total_orders(customer_id)
    return data


def create_order_from_chat(thread_id, containers_qty, delivery_date=None, container_type=None):
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
        container_type=container_type,
    )
    send_message(
        thread_id, "owner", "system",
        f"✅ Order confirmed: {containers_qty}x {order['container_label']}"
        + (f" para sa {delivery_date}" if delivery_date else "") + ". Salamat po!",
    )
    return order


def get_order(order_id):
    snap = db.collection(ORDERS).document(order_id).get()
    return snap.to_dict() if snap.exists else None


def list_orders(status=None):
    query = db.collection(ORDERS)
    if status:
        query = query.where("status", "==", status)
    docs = [d.to_dict() for d in query.stream()]
    docs.sort(key=lambda o: _parse_ts(o.get("order_date")) or 0, reverse=True)
    return docs


def accept_order(order_id):
    """Owner/staff confirms a 'pending' order. First step of the delivery
    lifecycle - see module docstring."""
    db.collection(ORDERS).document(order_id).update({"status": "accepted"})
    return get_order(order_id)


def decline_order(order_id, reason):
    """Owner/staff declines a 'pending' order (e.g. out of stock, can't
    deliver to that area/date, etc). The reason is REQUIRED (enforced by
    the route) and shown directly to the customer who placed it on their
    Order History page, plus sent to them via chat - same
    notify-the-customer pattern as mark_delivered()'s delivery message."""
    from models.chats import send_message

    order = get_order(order_id)
    if not order:
        raise ValueError("Order not found")

    db.collection(ORDERS).document(order_id).update({
        "status": "declined",
        "decline_reason": reason,
    })

    thread_id = order.get("chat_thread_id")
    if thread_id:
        send_message(thread_id, "owner", "system", f"❌ Pasensya na po, na-decline ang order nyo. Dahilan: {reason}")

    return get_order(order_id)


def confirm_delivery(order_id, customer_id):
    """Customer taps "Kumpirmahin" on their own Order History page after
    actually receiving the delivery. THIS is now the trigger for loyalty
    gallons (per owner's request - see models/loyalty.py module docstring
    for the safety reasoning), not mark_delivered() anymore. Idempotent -
    calling it again on an already-confirmed order is a silent no-op so a
    double-tap/refresh can't double-credit gallons."""
    from models import loyalty

    order = get_order(order_id)
    if not order:
        raise ValueError("Order not found")
    if order.get("customer_id") != customer_id:
        raise ValueError("Hindi mo pwedeng kumpirmahin ang order ng ibang customer.")
    if order.get("status") not in ("delivered", "paid", "utang"):
        raise ValueError("Hindi pa na-deliver ang order na ito.")
    if order.get("customer_confirmed"):
        return order

    db.collection(ORDERS).document(order_id).update({"customer_confirmed": True})

    delivered_qty = order.get("delivered_qty") or order.get("containers_qty") or 0
    gallons_per_unit = float(order.get("gallons_total", 0)) / float(order.get("containers_qty") or 1)
    loyalty.add_gallons(customer_id, gallons_per_unit * int(delivered_qty), order_id=order_id)

    return get_order(order_id)


def start_delivery(order_id):
    """Owner/staff sends an 'accepted' order out for delivery. No named
    rider is recorded (the branch's own external delivery riders aren't
    tracked in this system) - this just flips the status so the Customer
    Portal's tracker shows 'On the way'."""
    db.collection(ORDERS).document(order_id).update({"status": "on_delivery"})
    return get_order(order_id)


def mark_delivered(order_id, delivered_qty, payment_type, amount_collected,
                    photo_proof_url=None, delivered_by=None):
    """MODULE 3: staff marks an order delivered + uploads proof -> auto chat
    notification. MODULE 5: also feeds into daily sales totals (computed on
    read in routes/sales.py). `delivered_by` is just a free-text name (e.g.
    the logged-in staff member) for the record - not a FK to a riders
    collection, since individual riders aren't tracked in-system anymore."""
    from models.chats import send_message
    from models.customers import adjust_utang

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
        "delivered_by": delivered_by,
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

    # NOTE: loyalty gallons are NO LONGER awarded here - per owner's
    # request, they're only credited once the CUSTOMER confirms actual
    # receipt from their own portal (see confirm_delivery() above and
    # models/loyalty.py's module docstring for why this is still safe).

    thread_id = order.get("chat_thread_id")
    if thread_id:
        text = f"Na-deliver na po ang {delivered_qty} container/s nyo. Salamat sa pag-order!"
        send_message(thread_id, "owner", "system", text, message_type="text")
        if photo_proof_url:
            send_message(thread_id, "owner", "system", message_type="image", image_url=photo_proof_url)

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


def delete_order(order_id):
    """Hard delete - Owner or super-admin only (see routes/orders.py's
    @owner_or_super_admin_required delete()). Removes the order AND any
    linked delivery record so it doesn't leave orphaned rows in
    Reports/Sales totals. Does NOT reverse loyalty gallons or utang that
    were already applied when the order was delivered - undo those by
    hand first if the order being deleted was already paid/utang.

    Deletes each matched delivery by its own id (db.collection(...).document(id))
    rather than the snapshot's `.reference`, since the local mock Firestore
    (firebase_config.py) doesn't implement that attribute on query results -
    this way delete works identically against the mock and the real client."""
    db.collection(ORDERS).document(order_id).delete()
    for d in db.collection(DELIVERIES).where("order_id", "==", order_id).stream():
        db.collection(DELIVERIES).document(d.id).delete()


# Public alias - safe for other modules (e.g. routes/sales.py) to use directly
# instead of reaching into the "private" _parse_ts helper.
parse_ts = _parse_ts
