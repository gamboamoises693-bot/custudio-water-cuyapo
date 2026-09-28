"""
Firebase Admin (Firestore + Storage) initialization.

Real Firebase is used automatically when credentials are supplied via
FIREBASE_CRED_JSON (a full service-account JSON string, recommended for
Render) or FIREBASE_CRED_FILE (path to a local serviceAccountKey.json).

If NEITHER is set, this module falls back to a tiny local JSON-file-backed
mock of the Firestore client (same .collection()/.document() API surface
used across this app). This lets you run `python app.py` and `python seed.py`
immediately for local development/demo WITHOUT a Firebase project, and
switches to the real thing the moment you add credentials - no code changes
needed anywhere else in the app.
"""

import os
import json
import uuid
import threading
from datetime import datetime, timezone

USING_MOCK_DB = False


def _init_real_firebase():
    """Try to initialize the real firebase_admin SDK. Returns (db, bucket) or (None, None)."""
    cred_json = os.environ.get("FIREBASE_CRED_JSON")
    cred_file = os.environ.get("FIREBASE_CRED_FILE")

    if not cred_json and not cred_file:
        return None, None

    import firebase_admin
    from firebase_admin import credentials, firestore, storage

    if not firebase_admin._apps:
        # FIREBASE_CRED_FILE takes priority when both are set. A local key file
        # is much less likely to get corrupted than pasting the whole JSON blob
        # by hand (e.g. on a mobile device, where the private key's embedded
        # newlines can easily get mangled by the keyboard/clipboard).
        if cred_file:
            cred = credentials.Certificate(cred_file)
        else:
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)

        bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
        firebase_admin.initialize_app(
            cred, {"storageBucket": bucket_name} if bucket_name else {}
        )

    db = firestore.client()
    try:
        bucket = storage.bucket()
    except Exception:
        bucket = None
    return db, bucket


# ---------------------------------------------------------------------------
# Lightweight mock Firestore (JSON-file backed) - dev/demo fallback only.
# ---------------------------------------------------------------------------

_MOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
_MOCK_FILE = os.path.join(_MOCK_DIR, "mock_firestore.json")
_lock = threading.Lock()


def _load_mock():
    if not os.path.exists(_MOCK_FILE):
        return {}
    with open(_MOCK_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_mock(data):
    os.makedirs(_MOCK_DIR, exist_ok=True)
    with open(_MOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


class MockDocumentSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data or {}
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data else None


class MockDocumentRef:
    def __init__(self, collection_name, doc_id):
        self.collection_name = collection_name
        self.id = doc_id

    def get(self):
        with _lock:
            data = _load_mock()
            coll = data.get(self.collection_name, {})
            return MockDocumentSnapshot(self.id, coll.get(self.id))

    def set(self, values, merge=False):
        with _lock:
            data = _load_mock()
            coll = data.setdefault(self.collection_name, {})
            if merge and self.id in coll:
                coll[self.id].update(values)
            else:
                coll[self.id] = values
            _save_mock(data)

    def update(self, values):
        with _lock:
            data = _load_mock()
            coll = data.setdefault(self.collection_name, {})
            existing = coll.get(self.id, {})
            existing.update(values)
            coll[self.id] = existing
            _save_mock(data)

    def delete(self):
        with _lock:
            data = _load_mock()
            coll = data.get(self.collection_name, {})
            coll.pop(self.id, None)
            _save_mock(data)

    def collection(self, name):
        """Minimal subcollection support (e.g. customers/{id}/trusted_devices,
        used by MODULE 9's device-fingerprinting) - flattened into its own
        top-level key in the mock JSON store, namespaced by parent
        collection/doc so different parents' subcollections never collide."""
        return MockCollectionRef(f"{self.collection_name}/{self.id}/{name}")


class MockQuery:
    def __init__(self, collection_name, filters=None, order=None, limit_n=None):
        self.collection_name = collection_name
        self.filters = filters or []
        self.order = order
        self.limit_n = limit_n

    def where(self, field=None, op=None, value=None, filter=None):
        # Supports both old-style where(field, op, value) and new FieldFilter usage
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return MockQuery(
            self.collection_name,
            self.filters + [(field, op, value)],
            self.order,
            self.limit_n,
        )

    def order_by(self, field, direction=None):
        return MockQuery(self.collection_name, self.filters, (field, direction), self.limit_n)

    def limit(self, n):
        return MockQuery(self.collection_name, self.filters, self.order, n)

    def stream(self):
        data = _load_mock()
        coll = data.get(self.collection_name, {})
        items = [MockDocumentSnapshot(k, v) for k, v in coll.items()]

        def matches(snap):
            for field, op, value in self.filters:
                actual = snap._data.get(field)
                if op in ("==", "EQUAL"):
                    if actual != value:
                        return False
                elif op in ("<", "LESS_THAN"):
                    if not (actual is not None and actual < value):
                        return False
                elif op in (">", "GREATER_THAN"):
                    if not (actual is not None and actual > value):
                        return False
                elif op in ("<=",):
                    if not (actual is not None and actual <= value):
                        return False
                elif op in (">=",):
                    if not (actual is not None and actual >= value):
                        return False
            return True

        items = [s for s in items if matches(s)]

        if self.order:
            field, direction = self.order
            reverse = str(direction).upper() in ("DESCENDING", "DESC")
            items.sort(key=lambda s: (s._data.get(field) is None, s._data.get(field)), reverse=reverse)

        if self.limit_n:
            items = items[: self.limit_n]

        return items

    def get(self):
        return self.stream()


class MockCollectionRef(MockQuery):
    def __init__(self, collection_name):
        super().__init__(collection_name)

    def document(self, doc_id=None):
        if doc_id is None:
            doc_id = str(uuid.uuid4())
        return MockDocumentRef(self.collection_name, doc_id)

    def add(self, values):
        doc_id = str(uuid.uuid4())
        ref = MockDocumentRef(self.collection_name, doc_id)
        ref.set(values)
        return (None, ref)


class MockFirestoreClient:
    def collection(self, name):
        return MockCollectionRef(name)


class MockBucket:
    """No-op storage bucket stand-in for local/demo mode (image uploads are skipped)."""

    def blob(self, path):
        raise RuntimeError(
            "Firebase Storage isn't configured (running in mock/demo mode). "
            "Set FIREBASE_CRED_JSON + FIREBASE_STORAGE_BUCKET to enable image uploads."
        )


def server_timestamp():
    """Returns a value usable as a 'created_at'-style timestamp in both modes."""
    if USING_MOCK_DB:
        return datetime.now(timezone.utc).isoformat()
    from firebase_admin import firestore
    return firestore.SERVER_TIMESTAMP


# ---------------------------------------------------------------------------
# Public: db, bucket
# ---------------------------------------------------------------------------

try:
    db, bucket = _init_real_firebase()
except Exception as e:
    print(f"[firebase_config] Real Firebase init failed ({e}); falling back to mock DB.")
    db, bucket = None, None

if db is None:
    USING_MOCK_DB = True
    db = MockFirestoreClient()
    bucket = MockBucket()
    print(
        "[firebase_config] No FIREBASE_CRED_JSON/FIREBASE_CRED_FILE found -> "
        "running with local mock Firestore at instance/mock_firestore.json. "
        "This is fine for dev/demo; set real credentials before production use."
    )
else:
    print("[firebase_config] Connected to real Firebase Firestore.")
