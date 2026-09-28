"""
Simple session-based auth with 3 roles: owner, staff, rider.

NOTE: The spec mentions Firebase Auth. For MVP speed + zero extra Firebase
Console setup, this uses a lightweight Firestore-backed 'users' collection
with hashed passwords + Flask sessions instead. It's a drop-in: swap
`verify_login()` for a Firebase Auth call later without touching any route
that uses `login_required` / `role_required`, since those only look at
Flask's session.
"""

from functools import wraps
from flask import session, redirect, url_for, flash, request
from werkzeug.security import generate_password_hash, check_password_hash
from firebase_config import db

USERS = "users"


def create_user(email, password, name, role="staff", rider_id=None):
    ref = db.collection(USERS).document()
    user_id = ref.id
    data = {
        "id": user_id,
        "email": email.strip().lower(),
        "password_hash": generate_password_hash(password),
        "name": name,
        "role": role,  # owner | staff | rider
        "rider_id": rider_id,
    }
    ref.set(data)
    return data


def get_user_by_email(email):
    email = email.strip().lower()
    for d in db.collection(USERS).stream():
        u = d.to_dict()
        if u.get("email") == email:
            return u
    return None


def get_user(user_id):
    snap = db.collection(USERS).document(user_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


def list_users():
    """All owner/staff login accounts (NOT customers - see models/customers.py
    for the separate customer identity system). Used by the Accounts page
    (routes/accounts.py) so a super-admin (owner role) can see + manage
    every login on the system."""
    docs = [d.to_dict() for d in db.collection(USERS).stream()]
    docs.sort(key=lambda u: (u.get("role") != "owner", u.get("name", "")))
    return docs


def update_user_password(user_id, new_password):
    """Resets ANY login account's password (owner or staff) - used by the
    Accounts page so a super-admin doesn't need to touch Firestore/run a
    script every time someone forgets their password. Returns the updated
    user dict, or None if user_id doesn't exist."""
    ref = db.collection(USERS).document(user_id)
    if not ref.get().exists:
        return None
    ref.update({"password_hash": generate_password_hash(new_password)})
    return get_user(user_id)


def update_user_email(old_email, new_email):
    """Renames an existing login account's email (keeps password/role/name
    as-is). Used by one-off account-maintenance scripts, e.g. update_login_accounts.py.
    Returns the updated user dict, or None if old_email wasn't found."""
    old_email = old_email.strip().lower()
    new_email = new_email.strip().lower()
    for d in db.collection(USERS).stream():
        u = d.to_dict()
        if u.get("email") == old_email:
            d.reference.update({"email": new_email})
            return get_user_by_email(new_email)
    return None


def verify_login(email, password):
    user = get_user_by_email(email)
    if not user:
        return None
    if check_password_hash(user["password_hash"], password):
        return user
    return None


def current_user():
    if "user_id" not in session:
        return None
    return {
        "id": session.get("user_id"),
        "name": session.get("user_name"),
        "role": session.get("user_role"),
        "rider_id": session.get("rider_id"),
    }


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Mag-login muna po.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Mag-login muna po.", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if session.get("user_role") not in allowed_roles:
                flash("Wala kang access sa page na ito.", "danger")
                return redirect(url_for("sales.dashboard"))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
