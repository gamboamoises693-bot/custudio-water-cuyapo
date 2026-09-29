"""System-wide activity log + lightweight customer-UI engagement counters.

Two things live here, both feeding the Customer Portal Activity page
(routes/customers.py's activity_log(), Isesmo-only, same lock as everything
else on that page):

1. log_system_activity()/list_system_activity() - an audit trail of
   OWNER/STAFF actions (order accepted/declined/deleted, expense
   added/edited/deleted, account created/renamed/deleted, etc). This is
   separate from models/customer_auth.py's ACTIVITY_LOGS, which is what
   CUSTOMERS did on their own portal - this collection is what STAFF did.

2. bump_engagement()/get_engagement() - simple running counters for the two
   customer-UI engagement numbers the owner asked to see: how many times
   the promo video was played, and how many times the Omega Ice banner
   link was clicked. Kept as one small settings doc (not one row per
   click) since these are just totals, not something that needs a full
   per-event audit trail.

record_action() is the one-call convenience most routes actually use: it
logs to #1 above AND pushes a notification to Isesmo specifically (see
push_notify.send_push_to_isesmo) in a single call, pulling the acting
staff/customer name from the current Flask session so call sites don't
have to plumb it through manually. Per owner's explicit request: Isesmo
gets a notification for every action in the app.
"""

from firebase_config import db, server_timestamp

SYSTEM_ACTIVITY_LOGS = "system_activity_logs"
SETTINGS_DOC_ENGAGEMENT = "engagement_counters"


def log_system_activity(actor_name, actor_email, action, details="", actor_role=""):
    """Fire-and-forget: a logging hiccup must never block the real action
    that triggered it. actor_role is one of "developer"/"owner"/"staff"/
    "customer" (see record_action()'s resolution logic below) - lets the
    Customer Portal Activity page filter/group entries by who did them,
    per owner's request to see Owner vs Staff activity separately from
    Isesmo's own (developer) actions."""
    try:
        ref = db.collection(SYSTEM_ACTIVITY_LOGS).document()
        ref.set({
            "id": ref.id,
            "actor_name": actor_name or "System",
            "actor_email": actor_email or "",
            "actor_role": actor_role or "",
            "action": action,
            "details": details,
            "created_at": server_timestamp(),
        })
    except Exception as e:
        print(f"[activity] log_system_activity error: {e}")


def list_system_activity(limit=100, role=None):
    """role: optional filter - "developer"/"owner"/"staff"/"customer".
    Older log entries created before actor_role existed have "" stored -
    they're only excluded when a specific role filter is requested (an
    unfiltered "All" view still shows everything, old and new)."""
    from models.orders import parse_ts
    docs = [d.to_dict() for d in db.collection(SYSTEM_ACTIVITY_LOGS).stream()]
    docs.sort(key=lambda x: parse_ts(x.get("created_at")) or 0, reverse=True)
    if role:
        docs = [d for d in docs if d.get("actor_role") == role]
    return docs[:limit]


def record_action(action, details="", actor_name=None, actor_email=None):
    """Convenience wrapper called from routes right after a mutation
    actually succeeds (never before validating input, so a rejected
    action never gets logged/notified as if it happened). Logs to the
    System Activity feed (always works, no setup needed) and additionally
    pushes to Isesmo's own device if he has notifications enabled and
    VAPID keys are configured (silently does nothing otherwise - same
    graceful-degrade pattern as the rest of this app's push_notify calls).

    actor_role resolution: a STAFF/OWNER route never passes actor_name (it
    relies on the Flask staff session), so when there IS an active staff
    session (auth.current_user() returns something) we know this is a
    back-office action and classify by that account - "developer" for
    Isesmo/super-admin, "owner" for role="owner", "staff" otherwise. A
    CUSTOMER route (routes/customer_portal.py) always passes actor_name
    explicitly instead (no staff session exists in that request), which
    lands here in the else-branch as "customer".
    """
    from flask import session
    from auth import current_user
    import push_notify

    resolved_name = actor_name or session.get("user_name") or session.get("customer_name") or "System"
    resolved_email = actor_email if actor_email is not None else (session.get("user_email") or "")

    staff_user = current_user()
    if staff_user:
        if staff_user.get("is_super_admin"):
            resolved_role = "developer"
        elif staff_user.get("role") == "owner":
            resolved_role = "owner"
        else:
            resolved_role = "staff"
    else:
        resolved_role = "customer"

    log_system_activity(resolved_name, resolved_email, action, details, actor_role=resolved_role)
    push_notify.send_push_to_isesmo(db, f"🔔 {action}", details or resolved_name, url="/dashboard")


def bump_engagement(field):
    """Plain read-modify-write increment of one named counter (e.g.
    'video_plays', 'omega_link_clicks') in a single small settings doc.
    Not perfectly race-safe under heavy concurrent load, but more than
    accurate enough at this app's actual traffic volume (a handful of
    customers at a time), and works identically against both the real
    Firestore client and the local mock (which has no atomic-increment
    primitive)."""
    try:
        ref = db.collection("settings").document(SETTINGS_DOC_ENGAGEMENT)
        snap = ref.get()
        current = (snap.to_dict() or {}).get(field, 0) if snap.exists else 0
        ref.set({field: current + 1}, merge=True)
    except Exception as e:
        print(f"[activity] bump_engagement error: {e}")


def get_engagement():
    snap = db.collection("settings").document(SETTINGS_DOC_ENGAGEMENT).get()
    data = snap.to_dict() if snap.exists else {}
    return {
        "video_plays": data.get("video_plays", 0),
        "omega_link_clicks": data.get("omega_link_clicks", 0),
    }
