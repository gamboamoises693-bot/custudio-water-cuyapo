"""
MODULE 9: Customer Self-Service Portal - Auth.

Lets a CUSTOMER log into their own account (phone + password) and place
their own orders, instead of everything going through the owner/staff chat.
Modeled after the "Omega Ice" reseller portal (same author's other app):
phone+password login, forgot-password via a 6-digit code (no real SMS -
shown directly on screen, same deliberate choice already made on Omega Ice
to avoid per-SMS cost), a "trusted device" cookie that flags + logs a login
from a browser/device this account hasn't used before, and per-phone/per-
device rate limiting so the login and code-request endpoints can't be
brute-forced or spammed.

This is intentionally a SEPARATE identity system from auth.py (which is for
owner/staff/rider). A customer never gets a Flask session with `user_id` -
only `customer_id` - so `login_required`/`role_required` from auth.py never
accidentally grants a customer access to the owner dashboard, and this
module's `customer_login_required` never grants a customer session access
to owner-only routes either.
"""

import random
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import session, redirect, url_for, request
from werkzeug.security import generate_password_hash, check_password_hash

from firebase_config import db, server_timestamp
from models.customers import get_customer, update_customer, list_customers

OTPS = "customer_otps"
LOGIN_LOGS = "customer_login_logs"
ACTIVITY_LOGS = "customer_activity_logs"

DEVICE_COOKIE_NAME = "custudio_device_id"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year
OTP_VALID_MINUTES = 5

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(pwd):
    return generate_password_hash(pwd, method="pbkdf2:sha256", salt_length=16)


def verify_password(hash_val, pwd):
    if not hash_val or not pwd or len(hash_val) < 20:
        return False
    try:
        return check_password_hash(hash_val, pwd)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# In-memory rate limiting (per phone / per key). Resets on redeploy, which is
# fine - the goal is stopping a live brute-force burst, not a permanent ban.
# ---------------------------------------------------------------------------

_attempts = defaultdict(list)


def is_rate_limited(key, max_attempts=3, window_seconds=900):
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window_seconds]
    return len(_attempts[key]) >= max_attempts


def record_attempt(key):
    _attempts[key].append(time.time())


def clear_attempts(key):
    _attempts[key] = []


def clean_phone(phone):
    return "".join(c for c in (phone or "") if c.isdigit() or c == "+")


# ---------------------------------------------------------------------------
# Login / logging
# ---------------------------------------------------------------------------

def find_customer_by_phone(phone):
    phone = clean_phone(phone)
    for c in list_customers():
        if clean_phone(c.get("phone", "")) == phone:
            return c
    return None


def log_login(customer_id, customer_name, phone, success, reason=""):
    """Fire-and-forget: a logging hiccup must never block a real login."""
    try:
        ref = db.collection(LOGIN_LOGS).document()
        ref.set({
            "id": ref.id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "phone": phone,
            "success": success,
            "reason": reason,
            "created_at": server_timestamp(),
        })
    except Exception as e:
        print(f"[customer_auth] log_login error: {e}")


def log_activity(customer_id, customer_name, action, details=""):
    """Everything a customer DOES once already logged in (ordering,
    redeeming, etc.) - separate from log_login, which only covers the
    login attempt itself. Owner reviews this on the Customer Activity page."""
    try:
        ref = db.collection(ACTIVITY_LOGS).document()
        ref.set({
            "id": ref.id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "action": action,
            "details": details,
            "created_at": server_timestamp(),
        })
    except Exception as e:
        print(f"[customer_auth] log_activity error: {e}")


def verify_login(phone, password):
    """Returns (customer_dict_or_None, error_message_or_None)."""
    phone = clean_phone(phone)
    phone_key = f"cust_phone_{phone}"
    if is_rate_limited(phone_key, max_attempts=3, window_seconds=900):
        log_login(None, None, phone, False, "Locked out - too many attempts")
        return None, "Sobrang daming maling attempt. Subukan ulit pagkalipas ng 15 minuto."

    customer = find_customer_by_phone(phone)
    if not customer:
        log_login(None, None, phone, False, "Phone not registered")
        return None, "Hindi rehistrado ang phone number na ito. Makipag-ugnayan sa amin para makapag-register."

    stored_hash = customer.get("password_hash") or ""
    if not stored_hash:
        log_login(customer["id"], customer.get("name"), phone, False, "No password set")
        return None, "Wala ka pang password. Gamitin ang 'Nakalimutan ang password' para magtakda ng bago."

    if not verify_password(stored_hash, password):
        record_attempt(phone_key)
        log_login(customer["id"], customer.get("name"), phone, False, "Wrong password")
        return None, "Mali ang phone number o password."

    clear_attempts(phone_key)
    log_login(customer["id"], customer.get("name"), phone, True, "Manual login")
    return customer, None


# ---------------------------------------------------------------------------
# Forgot-password OTP (no SMS - shown directly, same as Omega Ice's own
# deliberate design decision, to avoid a paid SMS-gateway dependency for a
# low-stakes water-refill account. If this ever needs real SMS, wire
# SEMAPHORE_API_KEY the same way and send `otp` instead of returning it.)
# ---------------------------------------------------------------------------

def generate_otp():
    return "".join(random.choices("0123456789", k=6))


def request_otp(phone):
    """Returns (ok, otp_or_error_message)."""
    phone = clean_phone(phone)
    otp_key = f"otp_req_{phone}"
    if is_rate_limited(otp_key, max_attempts=3, window_seconds=900):
        return False, "Sobrang daming OTP request. Subukan ulit pagkalipas ng 15 minuto."

    if not find_customer_by_phone(phone):
        return False, "Hindi rehistrado ang phone number na ito."

    record_attempt(otp_key)
    otp = generate_otp()
    now = datetime.now(timezone.utc)
    ref = db.collection(OTPS).document()
    ref.set({
        "id": ref.id,
        "phone": phone,
        "otp": otp,
        "used": False,
        "expires_at": (now + timedelta(minutes=OTP_VALID_MINUTES)).isoformat(),
        "created_at": server_timestamp(),
    })
    return True, otp


def verify_otp_and_reset_password(phone, otp, new_password):
    """Returns (ok, error_message_or_None)."""
    phone = clean_phone(phone)
    otp = (otp or "").strip()
    verify_key = f"otp_verify_{phone}"
    if is_rate_limited(verify_key, max_attempts=5, window_seconds=300):
        return False, "Sobrang daming maling OTP attempt. Humingi ng bagong code pagkalipas ng ilang minuto."

    if len(new_password) < 4:
        return False, "Kailangan ng minimum 4 characters ang password."

    now = datetime.now(timezone.utc)
    matched_doc = None
    for d in db.collection(OTPS).where("phone", "==", phone).where("used", "==", False).stream():
        data = d.to_dict()
        if data.get("otp") != otp:
            continue
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (KeyError, ValueError):
            continue
        if now > expires_at:
            continue
        matched_doc = (d, data)
        break

    if not matched_doc:
        record_attempt(verify_key)
        return False, "Mali o expired na ang OTP code."

    clear_attempts(verify_key)
    clear_attempts(f"otp_req_{phone}")

    customer = find_customer_by_phone(phone)
    if not customer:
        return False, "Customer not found."

    update_customer(customer["id"], {"password_hash": hash_password(new_password)})
    matched_doc[0].reference.update({"used": True})
    log_activity(customer["id"], customer.get("name"), "Nag-reset ng password (OTP)")
    return True, None


def change_password(customer_id, current_password, new_password):
    """Change password while already logged in - requires the CURRENT
    password too, so grabbing an unlocked phone can't silently lock the
    real owner of the account out."""
    customer = get_customer(customer_id)
    if not customer:
        return False, "Customer not found."
    if not verify_password(customer.get("password_hash") or "", current_password):
        return False, "Mali ang kasalukuyang password."
    if len(new_password) < 4:
        return False, "Kailangan ng minimum 4 characters ang bagong password."
    update_customer(customer_id, {"password_hash": hash_password(new_password)})
    log_activity(customer_id, customer.get("name"), "Pinalitan ang sariling password")
    return True, None


# ---------------------------------------------------------------------------
# Trusted-device fingerprinting (cookie-based, per customer)
# ---------------------------------------------------------------------------

def _devices_ref(customer_id):
    return db.collection("customers").document(customer_id).collection("trusted_devices")


def check_and_register_device(customer_id):
    """Returns (is_new_device: bool, device_id: str). Always touches the
    device's last_seen so the owner can see recency; never blocks login on
    a hiccup (fails safe toward "treat as new")."""
    try:
        incoming_id = (request.cookies.get(DEVICE_COOKIE_NAME) or "").strip()
        devices_ref = _devices_ref(customer_id)
        is_new = not incoming_id or not devices_ref.document(incoming_id).get().exists
        device_id = incoming_id if incoming_id else secrets.token_hex(16)
        ua = (request.headers.get("User-Agent") or "")[:200]
        existing = devices_ref.document(device_id).get()
        added_at = existing.to_dict().get("added_at") if existing.exists else server_timestamp()
        devices_ref.document(device_id).set({
            "added_at": added_at,
            "last_seen": server_timestamp(),
            "user_agent": ua,
        })
        return is_new, device_id
    except Exception as e:
        print(f"[customer_auth] check_and_register_device error: {e}")
        return True, (request.cookies.get(DEVICE_COOKIE_NAME) or secrets.token_hex(16))


def set_device_cookie(resp, device_id):
    resp.set_cookie(
        DEVICE_COOKIE_NAME, device_id,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True, secure=True, samesite="Lax", path="/",
    )
    return resp


# ---------------------------------------------------------------------------
# Session guard for customer-portal routes
# ---------------------------------------------------------------------------

def customer_login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            return redirect(url_for("customer_portal.login_page"))
        return view_func(*args, **kwargs)
    return wrapped
