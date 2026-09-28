"""
Optional Web Push for the customer portal - lets a customer get a phone
notification ("Order confirmed!", "Bagong device login") even when the app
isn't open, same idea as Omega Ice's push setup.

Gracefully disabled (rest of the app works exactly the same either way) when
`pywebpush` isn't installed or VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY aren't set -
see README for how to generate a free VAPID keypair. This mirrors the same
optional-dependency fallback pattern already used for Firebase Storage
uploads in routes/chats.py and routes/deliveries.py.
"""

import os
import hashlib

try:
    from pywebpush import webpush, WebPushException
    PUSH_LIB_AVAILABLE = True
except ImportError:
    PUSH_LIB_AVAILABLE = False

# .env/Render env vars can't hold real newlines in a single-line value, so
# generate_vapid_keys.py prints the PEM with literal "\n" text - unescape it
# back into a real multi-line PEM here before it's used.
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").replace("\\n", "\n")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_SUB = os.environ.get("VAPID_CLAIMS_SUB", "mailto:admin@example.com")
PUSH_ENABLED = bool(PUSH_LIB_AVAILABLE and VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)


def subscription_id(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()[:32]


def send_push_to_customer(db, customer_id, title, body, url="/customer"):
    """Fire-and-forget: never raises, never blocks the caller. Sends to
    every subscription this customer has registered (e.g. more than one
    device)."""
    if not PUSH_ENABLED:
        return
    try:
        import json
        for doc in db.collection("push_subscriptions").where("customer_id", "==", customer_id).stream():
            sub = doc.to_dict()
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": sub["keys"],
                    },
                    data=json.dumps({"title": title, "body": body, "url": url}),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_CLAIMS_SUB},
                )
            except WebPushException as e:
                # A dead/expired subscription is expected over time (user
                # uninstalled, cleared site data, etc.) - clean it up instead
                # of retrying it forever.
                print(f"[push_notify] webpush failed for {doc.id}: {e}")
                if "410" in str(e) or "404" in str(e):
                    doc.reference.delete()
    except Exception as e:
        print(f"[push_notify] send_push_to_customer error: {e}")


def send_push_to_owner(db, title, body, url="/orders"):
    """Notifies every OWNER/STAFF push subscription (e.g. 'New order placed
    by a customer online') - separate collection key (role=owner) from the
    per-customer ones above."""
    if not PUSH_ENABLED:
        return
    try:
        import json
        for doc in db.collection("push_subscriptions").where("role", "==", "owner").stream():
            sub = doc.to_dict()
            try:
                webpush(
                    subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=json.dumps({"title": title, "body": body, "url": url}),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_CLAIMS_SUB},
                )
            except WebPushException as e:
                print(f"[push_notify] webpush failed for {doc.id}: {e}")
                if "410" in str(e) or "404" in str(e):
                    doc.reference.delete()
    except Exception as e:
        print(f"[push_notify] send_push_to_owner error: {e}")
