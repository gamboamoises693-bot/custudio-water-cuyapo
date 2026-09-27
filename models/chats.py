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

THREADS = "chat_threads"
MESSAGES = "chat_messages"

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

    def sort_key(t):
        ts = t.get("last_message_time")
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    docs.sort(key=sort_key, reverse=True)
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

    def sort_key(m):
        ts = m.get("timestamp")
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)
        return datetime.min.replace(tzinfo=timezone.utc)

    docs.sort(key=sort_key)
    return docs[-limit:]


def mark_thread_seen_by_owner(thread_id):
    db.collection(THREADS).document(thread_id).update({"unread_count_owner": 0})


def mark_thread_seen_by_customer(thread_id):
    db.collection(THREADS).document(thread_id).update({"unread_count_customer": 0})


def total_unread_for_owner():
    threads = list_threads()
    return sum(t.get("unread_count_owner", 0) for t in threads)
