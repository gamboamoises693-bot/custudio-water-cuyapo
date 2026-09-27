"""
Inventory & Tank model - Firestore collections: tanks, inventory_items

tanks: id, tank_type (raw/purified), level_percent, last_updated
inventory_items: id, item_name (empty_bottle/cap/seal/plastic),
                 stock_qty, low_alert_qty
"""

from firebase_config import db, server_timestamp

TANKS = "tanks"
ITEMS = "inventory_items"

LOW_TANK_THRESHOLD = 50  # percent - "purified tank < 50% => red alert"


def create_tank(tank_type, level_percent):
    ref = db.collection(TANKS).document()
    tank_id = ref.id
    data = {
        "id": tank_id,
        "tank_type": tank_type,
        "level_percent": float(level_percent),
        "last_updated": server_timestamp(),
    }
    ref.set(data)
    return data


def list_tanks():
    return [d.to_dict() for d in db.collection(TANKS).stream()]


def update_tank_level(tank_id, level_percent):
    db.collection(TANKS).document(tank_id).update({
        "level_percent": float(level_percent),
        "last_updated": server_timestamp(),
    })


def get_low_tank_alerts():
    """Returns tanks (usually 'purified') below the low threshold, for the dashboard banner."""
    tanks = list_tanks()
    return [t for t in tanks if t.get("tank_type") == "purified" and t.get("level_percent", 100) < LOW_TANK_THRESHOLD]


def create_item(item_name, stock_qty, low_alert_qty):
    ref = db.collection(ITEMS).document()
    item_id = ref.id
    data = {
        "id": item_id,
        "item_name": item_name,
        "stock_qty": int(stock_qty),
        "low_alert_qty": int(low_alert_qty),
    }
    ref.set(data)
    return data


def list_items():
    return [d.to_dict() for d in db.collection(ITEMS).stream()]


def update_item_stock(item_id, stock_qty):
    db.collection(ITEMS).document(item_id).update({"stock_qty": int(stock_qty)})


def adjust_item_stock(item_id, delta):
    snap = db.collection(ITEMS).document(item_id).get()
    if not snap.exists:
        return None
    current = snap.to_dict().get("stock_qty", 0)
    new_qty = max(0, current + delta)
    update_item_stock(item_id, new_qty)
    return new_qty


def get_low_stock_items():
    items = list_items()
    return [i for i in items if i.get("stock_qty", 0) <= i.get("low_alert_qty", 0)]


def delete_item(item_id):
    db.collection(ITEMS).document(item_id).delete()
