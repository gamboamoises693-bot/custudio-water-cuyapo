"""
MODULE 9 (cont'd): Loyalty card + "Free Spin" mini-game for customers who
order through the self-service portal.

Rewritten (again) to match Custodio Water's own printed loyalty
card + price list exactly: "For every 100 GALLONS, avail FREE 5 GALLONS
for next delivery!" Since the price list has MULTIPLE container types of
different sizes (1 Gallon Slim/Round, 6 Gallon, 6-10 Liters), the card now
tracks cumulative GALLONS purchased (not a flat "1 punch per container" -
that stopped making sense the moment containers stopped being one uniform
size). See models/pricing.py for the container catalog and its gallons
figure per type.

Redeeming the "5 free gallons": since a customer might redeem while buying
ANY container type (not necessarily 1-gallon ones), the 5 free gallons are
applied as a peso discount at THAT order's own per-gallon rate
(unit_price / gallons_per_container) - so redeeming never depends on which
container type they happen to be ordering that day.

Deliberate adaptation vs. Omega Ice: a customer here can NEVER self-mark
their own order "Delivered" - that status still also carries the payment
info (amount collected, cash/gcash/utang) that only whoever processed the
handover can honestly supply, and stays OWNER/STAFF-only (Deliveries page,
mark_delivered()).

UPDATED per owner's request: loyalty gallons are now only awarded once the
CUSTOMER confirms they actually received the delivery (models/orders.py's
confirm_delivery(), triggered from their own portal's Order History page)
- NOT automatically the moment staff marks it delivered. This doesn't
reopen the old farming risk: staff still has to mark the order
delivered/paid first (the customer can't fabricate a delivery record),
the customer just acknowledges receipt of one that already exists before
the points post.
"""

import random
from datetime import datetime, timezone

from firebase_config import db, server_timestamp
from models.customers import get_customer, update_customer

ORDER_SPINS = "order_spins"
LOYALTY_SETTINGS_DOC = "loyalty_settings"
LOYALTY_LOG = "loyalty_log"

GALLONS_PER_REWARD = 100        # 100 gallons bought...
FREE_GALLONS_PER_REWARD = 5     # ...earns 5 free gallons on the next order

FREE_SPIN_CLAIM_WINDOW_MINUTES = 30


def _parse_ts(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    if hasattr(value, "timestamp"):
        try:
            return value.timestamp()
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Program-wide pause switch (owner can temporarily suspend the whole program,
# e.g. during a stock shortage)
# ---------------------------------------------------------------------------

def _settings_ref():
    return db.collection("settings").document(LOYALTY_SETTINGS_DOC)


def get_settings():
    snap = _settings_ref().get()
    if snap.exists:
        return snap.to_dict()
    return {"paused": False}


def is_program_paused():
    return bool(get_settings().get("paused", False))


def set_program_paused(paused: bool):
    _settings_ref().set({"paused": bool(paused)}, merge=True)


# ---------------------------------------------------------------------------
# The loyalty card itself (kept directly on the customer doc:
# loyalty_gallons, loyalty_free_gallons)
# ---------------------------------------------------------------------------

def get_loyalty_card(customer_id):
    """Never raises - a brand-new customer with no gallons yet just shows
    0/100 and 0 free gallons."""
    customer = get_customer(customer_id) or {}
    gallons = float(customer.get("loyalty_gallons", 0) or 0)
    free_gallons = float(customer.get("loyalty_free_gallons", 0) or 0)
    return {
        "gallons": gallons,
        "gallons_needed": GALLONS_PER_REWARD,
        "free_gallons": free_gallons,
        "paused": is_program_paused(),
    }


def add_gallons(customer_id, gallons, order_id=None):
    """Call this once an order is actually marked delivered/paid - NEVER on
    mere order placement (matches how the physical card works: staff only
    stamps it once the containers really go out). Adds `gallons`; every
    time the running total crosses a multiple of GALLONS_PER_REWARD, grants
    that many free-gallon credits and carries over the remainder (never
    lost, never resets to zero on a partial crossing)."""
    gallons = float(gallons or 0)
    if gallons <= 0:
        return get_loyalty_card(customer_id)

    customer = get_customer(customer_id)
    if not customer:
        return None

    old_gallons = float(customer.get("loyalty_gallons", 0) or 0)
    old_free = float(customer.get("loyalty_free_gallons", 0) or 0)

    new_total = old_gallons + gallons
    rewards_earned = int(new_total // GALLONS_PER_REWARD)
    remaining_gallons = new_total - (rewards_earned * GALLONS_PER_REWARD)
    new_free = old_free + (rewards_earned * FREE_GALLONS_PER_REWARD)

    update_customer(customer_id, {
        "loyalty_gallons": remaining_gallons,
        "loyalty_free_gallons": new_free,
        "loyalty_updated_at": server_timestamp(),
    })

    # MODULE 9 (cont'd): Per-Customer Loyalty History Log - append-only
    # audit trail so owner/staff and the customer themselves can see
    # exactly when/why their loyalty gallons moved (see customer_history.html).
    note = f"+{rewards_earned * FREE_GALLONS_PER_REWARD} free gallons na-unlock!" if rewards_earned > 0 else ""
    log_loyalty_event(customer_id, "earned", gallons, note=note, order_id=order_id)

    return {
        "gallons": remaining_gallons,
        "gallons_needed": GALLONS_PER_REWARD,
        "free_gallons": new_free,
        "rewards_earned_this_time": rewards_earned,
    }


def apply_free_gallons(customer_id, order_gallons, order_amount, order_id=None):
    """Called when a NEW order is placed - auto-applies as many free
    gallons as the customer currently has, capped at how many gallons are
    actually in THIS order (never lets an order go negative, never applies
    more free gallons than were ordered). The peso discount is computed at
    this order's own per-gallon rate, so it's fair whatever container type
    they're buying. Returns (free_gallons_applied, discount_amount,
    billable_amount)."""
    if is_program_paused() or order_gallons <= 0:
        return 0.0, 0.0, order_amount

    customer = get_customer(customer_id)
    if not customer:
        return 0.0, 0.0, order_amount

    credit = float(customer.get("loyalty_free_gallons", 0) or 0)
    if credit <= 0:
        return 0.0, 0.0, order_amount

    per_gallon_rate = order_amount / order_gallons if order_gallons else 0
    free_gallons_applied = min(credit, order_gallons)
    discount_amount = round(free_gallons_applied * per_gallon_rate, 2)
    discount_amount = min(discount_amount, order_amount)  # never a negative bill

    update_customer(customer_id, {"loyalty_free_gallons": credit - free_gallons_applied})
    log_loyalty_event(customer_id, "redeemed", free_gallons_applied,
                       note=f"₱{discount_amount:.2f} discount na-apply", order_id=order_id)
    return free_gallons_applied, discount_amount, round(order_amount - discount_amount, 2)


# ---------------------------------------------------------------------------
# Promo monitoring (owner requested 3 things: a dashboard widget for
# customers close to a reward, a redemption report for the Reports page,
# and a per-customer history log for the Customer Portal)
# ---------------------------------------------------------------------------

def list_customers_near_reward(threshold=80):
    """Customers whose current loyalty_gallons is >= `threshold` (out of
    GALLONS_PER_REWARD) - i.e. close to unlocking their next 5 free
    gallons. Sorted highest-gallons-first so the owner sees who's closest."""
    from models.customers import list_customers

    customers = list_customers()
    near = [c for c in customers if float(c.get("loyalty_gallons", 0) or 0) >= threshold]
    near.sort(key=lambda c: float(c.get("loyalty_gallons", 0) or 0), reverse=True)
    return near


def get_redemption_summary(period_orders):
    """`period_orders`: a list of order dicts already filtered to the
    desired date range by the caller (see routes/sales.py's reports(),
    which filters by order_date the same way it already filters
    deliveries). Returns totals for how much loyalty promo was redeemed
    in that period."""
    redemptions = [o for o in period_orders if float(o.get("free_gallons_applied", 0) or 0) > 0]
    return {
        "redemption_count": len(redemptions),
        "total_free_gallons": sum(float(o.get("free_gallons_applied", 0) or 0) for o in redemptions),
        "total_discount_amount": sum(float(o.get("loyalty_discount_amount", 0) or 0) for o in redemptions),
    }


def log_loyalty_event(customer_id, event_type, gallons, note="", order_id=None):
    """Append-only audit trail. `event_type`: 'earned' (from add_gallons,
    i.e. an order got delivered), 'redeemed' (from apply_free_gallons,
    i.e. free gallons discounted a new order). Never raises - a logging
    failure should never block the loyalty gallons themselves from being
    awarded/applied."""
    try:
        ref = db.collection(LOYALTY_LOG).document()
        ref.set({
            "id": ref.id,
            "customer_id": customer_id,
            "event_type": event_type,
            "gallons": round(float(gallons or 0), 2),
            "note": note or "",
            "order_id": order_id,
            "timestamp": server_timestamp(),
        })
    except Exception:
        pass


def get_loyalty_log(customer_id, limit=50):
    """Most-recent-first list of this customer's loyalty_log entries."""
    docs = [
        d.to_dict() for d in
        db.collection(LOYALTY_LOG).where("customer_id", "==", customer_id).stream()
    ]
    docs.sort(key=lambda e: _parse_ts(e.get("timestamp")) or 0, reverse=True)
    return docs[:limit]


# Public alias - safe for other modules (e.g. routes/customer_portal.py) to
# use directly instead of reaching into the "private" _parse_ts helper.
parse_ts = _parse_ts


# ---------------------------------------------------------------------------
# Free Spin (unlocks once staff has marked the order paid/utang) - a
# light bonus that awards EXTRA GALLONS (not separate points), so it feeds
# straight into the same single loyalty card above.
# ---------------------------------------------------------------------------

def spin_prize():
    """Small chance of 1-2 bonus gallons, mostly 0 ('better luck next
    order') - a light-touch incentive on top of the gallons they already
    earned for this order."""
    return random.choices([0, 1, 2], weights=[55, 30, 15])[0]


def compute_spin_eligibility(order, delivery=None):
    """Returns (can_spin: bool, reason_if_not). Eligible once the order is
    paid/utang (staff-confirmed delivered), within the claim window, and
    hasn't already been spun."""
    if order.get("spin_claimed"):
        return False, "Na-claim mo na ang Free Spin ng order na ito."
    if order.get("status") not in ("paid", "utang"):
        return False, "Hintayin munang ma-deliver ang order bago mag-Free Spin."

    delivered_at = _parse_ts(delivery.get("delivered_at")) if delivery else None
    if delivered_at is None:
        return False, "Order not yet delivered."
    minutes_since = (datetime.now(timezone.utc).timestamp() - delivered_at) / 60
    if minutes_since > FREE_SPIN_CLAIM_WINDOW_MINUTES:
        return False, f"Lumipas na ang {FREE_SPIN_CLAIM_WINDOW_MINUTES}-minutong window para sa Free Spin."
    return True, None


def claim_spin(order_id, customer_id):
    """Returns (ok, result_dict_or_error_message)."""
    from models.orders import get_order

    order = get_order(order_id)
    if not order:
        return False, "Order not found."
    if order.get("customer_id") != customer_id:
        return False, "Hindi mo order ito."

    delivery = None
    for d in db.collection("deliveries").where("order_id", "==", order_id).limit(1).stream():
        delivery = d.to_dict()

    can_spin, reason = compute_spin_eligibility(order, delivery)
    if not can_spin:
        return False, reason

    if is_program_paused():
        return False, "Pansamantalang naka-pause ang Loyalty Card program - wala munang Free Spin ngayon."

    bonus_gallons = spin_prize()
    db.collection("orders").document(order_id).update({
        "spin_claimed": True,
        "spin_bonus_gallons": bonus_gallons,
        "spin_claimed_at": server_timestamp(),
    })
    spin_ref = db.collection(ORDER_SPINS).document()
    spin_ref.set({
        "id": spin_ref.id,
        "order_id": order_id,
        "customer_id": customer_id,
        "bonus_gallons": bonus_gallons,
        "timestamp": server_timestamp(),
    })
    card = get_loyalty_card(customer_id)
    if bonus_gallons > 0:
        card = add_gallons(customer_id, bonus_gallons, order_id=order_id)
    return True, {"bonus_gallons": bonus_gallons, "card": card}
