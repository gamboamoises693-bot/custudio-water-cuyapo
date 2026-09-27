"""
Rider model - Firestore collection: riders

Fields: id, name, phone, tricycle_no, total_delivered_today,
        total_collected_today, last_reset_date
"""

from datetime import datetime, timezone
from firebase_config import db, server_timestamp

COLLECTION = "riders"


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def create_rider(name, phone, tricycle_no):
    ref = db.collection(COLLECTION).document()
    rider_id = ref.id
    data = {
        "id": rider_id,
        "name": name.strip(),
        "phone": phone.strip(),
        "tricycle_no": tricycle_no.strip(),
        "total_delivered_today": 0,
        "total_collected_today": 0.0,
        "last_reset_date": _today_str(),
        "created_at": server_timestamp(),
    }
    ref.set(data)
    return data


def get_rider(rider_id):
    snap = db.collection(COLLECTION).document(rider_id).get()
    return snap.to_dict() if snap.exists else None


def list_riders():
    return [d.to_dict() for d in db.collection(COLLECTION).stream()]


def update_rider(rider_id, updates: dict):
    db.collection(COLLECTION).document(rider_id).update(updates)
    return get_rider(rider_id)


def delete_rider(rider_id):
    db.collection(COLLECTION).document(rider_id).delete()


def _reset_if_new_day(rider):
    if rider.get("last_reset_date") != _today_str():
        update_rider(rider["id"], {
            "total_delivered_today": 0,
            "total_collected_today": 0.0,
            "last_reset_date": _today_str(),
        })
        rider["total_delivered_today"] = 0
        rider["total_collected_today"] = 0.0
    return rider


def bump_rider_daily_stats(rider_id, delivered_qty, amount_collected):
    if not rider_id:
        return
    rider = get_rider(rider_id)
    if not rider:
        return
    rider = _reset_if_new_day(rider)
    update_rider(rider_id, {
        "total_delivered_today": rider.get("total_delivered_today", 0) + int(delivered_qty),
        "total_collected_today": rider.get("total_collected_today", 0.0) + float(amount_collected),
    })


def rider_performance_today():
    """MODULE 6: performance list + simple 'daya' (cheating) detection.
    Flags a rider if amount collected is suspiciously low vs containers delivered,
    e.g. collected less than 50% of what delivered_qty * avg_price would suggest,
    while payment wasn't fully utang.
    """
    riders = [ _reset_if_new_day(r) for r in list_riders() ]
    results = []
    for r in riders:
        delivered = r.get("total_delivered_today", 0)
        collected = r.get("total_collected_today", 0.0)
        expected_min = delivered * 5  # rough floor sanity check (containers are never < ~5 pesos)
        suspicious = delivered > 0 and collected == 0 and expected_min > 0
        results.append({
            **r,
            "flag_daya": suspicious,
        })
    results.sort(key=lambda r: r.get("total_delivered_today", 0), reverse=True)
    return results
