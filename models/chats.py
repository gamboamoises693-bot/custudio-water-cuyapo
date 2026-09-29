"""
Chat model - Firestore collections: chat_threads, chat_messages

chat_threads: id, customer_id, customer_name, last_message,
              last_message_time, unread_count_owner, unread_count_customer
chat_messages: id, thread_id, sender_type (owner/staff/rider/customer),
               sender_id, message_text, message_type (text/image),
               image_url, timestamp, seen
"""

from datetime import datetime, timezone
from firebase_config import db, server_timestamp
from models.timeutil import parse_ts

THREADS = "chat_threads"
MESSAGES = "chat_messages"

_EPOCH_MIN = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(ts):
    """Chronological sort key for a stored timestamp.

    ROOT CAUSE of "hindi sunod-sunod yung ayos ng mga message": this used
    to only handle `isinstance(ts, str)` (true for the local mock DB, which
    stores an ISO string). On REAL Firebase, a timestamp read back from
    Firestore is a `DatetimeWithNanoseconds` object, not a string - so the
    old code silently fell through to `datetime.min` for EVERY message and
    EVERY thread, which means nothing was actually being sorted by time at
    all; messages just stayed in whatever arbitrary order Firestore's
    `.stream()` happened to return them in. `parse_ts()` (models/timeutil.py)
    handles both shapes (string and datetime/DatetimeWithNanoseconds), so
    this now sorts correctly on both the local mock DB and real Firebase."""
    return parse_ts(ts) or _EPOCH_MIN

QUICK_REPLIES = [
    "On the way na po tubig nyo",
    "Magkano po utang ko?",
    "5 containers po bukas 7AM",
    "Salamat po sa order!",
    "Paalala po: bukas ang schedule ng delivery nyo",
]


def create_thread_for_customer(customer_id, customer_name):
    thread_ref = db.collection(THREADS).document()
    thread_id = thread_ref.id
    thread_ref.set({
        "id": thread_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "last_message": "",
        "last_message_time": server_timestamp(),
        "unread_count_owner": 0,
        "unread_count_customer": 0,
    })
    return thread_id


def get_thread(thread_id):
    snap = db.collection(THREADS).document(thread_id).get()
    return snap.to_dict() if snap.exists else None


def list_threads():
    """All threads sorted by most recent activity first (for the Messenger-style list)."""
    docs = [d.to_dict() for d in db.collection(THREADS).stream()]
    docs.sort(key=lambda t: _sort_key(t.get("last_message_time")), reverse=True)
    return docs


def send_message(thread_id, sender_type, sender_id, message_text=None, message_type="text", image_url=None):
    """Adds a message to a thread and updates the thread's preview/unread counters.
    sender_type: 'owner' | 'staff' | 'rider' | 'customer'
    """
    msg_ref = db.collection(MESSAGES).document()
    msg_id = msg_ref.id
    now = server_timestamp()

    msg_ref.set({
        "id": msg_id,
        "thread_id": thread_id,
        "sender_type": sender_type,
        "sender_id": sender_id,
        "message_text": message_text or "",
        "message_type": message_type,
        "image_url": image_url,
        "timestamp": now,
        "seen": False,
    })

    preview = "📷 Image" if message_type == "image" else (message_text or "")
    thread = get_thread(thread_id) or {}
    unread_owner = thread.get("unread_count_owner", 0)
    unread_customer = thread.get("unread_count_customer", 0)

    if sender_type == "customer":
        unread_owner += 1
    else:
        unread_customer += 1

    db.collection(THREADS).document(thread_id).update({
        "last_message": preview,
        "last_message_time": now,
        "unread_count_owner": unread_owner,
        "unread_count_customer": unread_customer,
    })
    return msg_id


def list_messages(thread_id, limit=200):
    query = db.collection(MESSAGES).where("thread_id", "==", thread_id)
    docs = [d.to_dict() for d in query.stream()]
    docs.sort(key=lambda m: _sort_key(m.get("timestamp")))
    return docs[-limit:]


def last_owner_message_id(messages):
    """Returns the id of the most recent OWNER/STAFF message in a
    chronologically-sorted `messages` list (see list_messages()), or None
    if the customer hasn't been messaged yet. Used to show a single
    'Naipadala/Nakita na' (sent/seen) indicator only on the latest outgoing
    message - Messenger-style - instead of one on every single bubble."""
    for m in reversed(messages):
        if m.get("sender_type") in ("owner", "staff"):
            return m.get("id")
    return None


def mark_thread_seen_by_owner(thread_id):
    db.collection(THREADS).document(thread_id).update({"unread_count_owner": 0})


def mark_thread_seen_by_customer(thread_id):
    db.collection(THREADS).document(thread_id).update({"unread_count_customer": 0})


def mark_messages_seen_by_customer(thread_id):
    """Flips seen=True on every OWNER/STAFF message in this thread. Call
    this whenever the customer actually opens or polls their chat page, so
    the owner's side (chat.html) can show a 'Nakita na' (seen) indicator on
    their last sent message - per owner's request to know kung nakita na ng
    customer ang message nila. Only touches messages that aren't already
    marked seen, to avoid a needless Firestore write on every poll."""
    query = db.collection(MESSAGES).where("thread_id", "==", thread_id)
    for doc in query.stream():
        data = doc.to_dict()
        if data.get("sender_type") in ("owner", "staff") and not data.get("seen"):
            db.collection(MESSAGES).document(doc.id).update({"seen": True})


def total_unread_for_owner():
    threads = list_threads()
    return sum(t.get("unread_count_owner", 0) for t in threads)
