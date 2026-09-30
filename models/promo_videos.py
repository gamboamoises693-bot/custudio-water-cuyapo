"""Admin-uploaded promo videos - the direct-upload alternative to the
original GitHub-web-upload workflow.

Each record here is a small Firestore doc pointing at a video file that
lives in FIREBASE STORAGE (not this server's local disk). routes/
customer_portal.py's _list_promo_videos() merges these with any video
files still sitting in static/videos/ (the old GitHub-upload workflow) -
both ways of adding a promo video keep working side by side, so nothing
Isesmo already uploaded via GitHub ever disappears.

WHY NOT just save the uploaded file straight into static/videos/ on the
server: Render's web service filesystem is EPHEMERAL - anything written
there at runtime disappears on every redeploy/restart (a fresh container
starts from the GitHub repo's own contents each time, nothing more). A
video "uploaded" that way would silently vanish again after the next
deploy - literally the same "nawala yung video" bug already diagnosed
earlier in this app's history, just self-inflicted this time. Firebase
Storage is the actual persistent home for anything uploaded at runtime
(same pattern already used for chat images / delivery proof photos - see
routes/chats.py's _upload_chat_image())."""

from firebase_config import db, server_timestamp

PROMO_VIDEOS = "promo_videos"


def list_promo_video_docs():
    """All admin-uploaded promo videos, as plain dicts:
    [{id, filename, title, url, storage_path, uploaded_at}, ...].
    `filename` is a server-generated unique key (uuid-based, see
    routes/customers.py's video_playlist_upload()) - NOT the original
    uploaded file's name, so it can never collide with a file manually
    uploaded via GitHub into static/videos/."""
    return [d.to_dict() for d in db.collection(PROMO_VIDEOS).stream()]


def create_promo_video_doc(filename, title, url, storage_path):
    ref = db.collection(PROMO_VIDEOS).document()
    data = {
        "id": ref.id,
        "filename": filename,
        "title": title,
        "url": url,
        "storage_path": storage_path,
        "uploaded_at": server_timestamp(),
    }
    ref.set(data)
    return data


def delete_promo_video_doc(doc_id):
    """Returns the deleted doc's dict (so the caller can also remove its
    Storage blob), or None if doc_id didn't exist."""
    ref = db.collection(PROMO_VIDEOS).document(doc_id)
    snap = ref.get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    ref.delete()
    return data
