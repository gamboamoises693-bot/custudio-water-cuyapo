"""MODULE 9: Customer Self-Service Portal.


Lets a customer log into THEIR OWN account (phone + password, no owner/
staff email account needed) and place their own orders, see their order
history, and earn free containers on their punch-card-style loyalty program
- modeled after the "Omega Ice" reseller portal (same author's other app),
adapted to Custodio Water's order model. See models/customer_auth.py and
models/loyalty.py for the
business logic; this file is just the routes.

Access rule used throughout: either the matching logged-in customer
(session['customer_id'] == the :customer_id in the URL), or a logged-in
owner/staff member (session['user_id'] set via auth.py) looking up a
customer on their behalf (e.g. redeeming a reward in person). Never both
mixed - logging in as a customer clears any leftover owner/staff session,
and vice versa (auth.py's login already does the latter).
"""

from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, make_response

from firebase_config import db
from models import customer_auth
from models import loyalty
from models import chats as chats_model
from models.customers import get_customer, get_customer_by_qr_token
from models.orders import get_order, list_orders, create_order
from models.pricing import list_container_types, get_container_type, DEFAULT_CONTAINER_TYPE
import push_notify

customer_portal_bp = Blueprint("customer_portal", __name__)


def _is_owner_or_staff():
    return bool(session.get("user_id"))


def _can_access(customer_id):
    if session.get("customer_id") == customer_id:
        return True
    return _is_owner_or_staff()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/customer")
def login_page():
    if session.get("customer_id"):
        return redirect(url_for("customer_portal.dashboard_page", customer_id=session["customer_id"]))
    return render_template("customer_login.html")


@customer_portal_bp.route("/customer/qr-login/<token>")
def qr_login(token):
    """Tap/scan-to-login: the QR printed for a customer (see
    routes/customers.py's qr_image()) encodes THIS url. Opening it logs the
    customer straight into their own Customer Portal dashboard - no
    phone/password typing needed, per owner's explicit request. Reuses the
    same device-fingerprinting + login-log trail as the regular phone+
    password login (api_login() above) so this still shows up in the
    Customer Activity page for audit purposes.

    NOTE (deliberate tradeoff, already flagged to and confirmed by the
    owner): since this needs no password, anyone holding the physical
    QR/card can log in as that customer. If a card is ever lost, use
    Customers > 🔄 QR to invalidate the old code and issue a new one."""
    customer = get_customer_by_qr_token(token)
    if not customer:
        flash("Invalid o expired na ang QR code na ito.", "danger")
        return redirect(url_for("customer_portal.login_page"))

    # Clear any leftover owner/staff identity - same rule as api_login().
    session.pop("user_id", None)
    session.pop("user_name", None)
    session.pop("user_role", None)
    session.pop("rider_id", None)
    session["customer_id"] = customer["id"]
    session["customer_name"] = customer.get("name")

    is_new_device, device_id = customer_auth.check_and_register_device(customer["id"])
    customer_auth.log_login(customer["id"], customer.get("name"), customer.get("phone", ""), True, "QR auto-login")
    if is_new_device:
        push_notify.send_push_to_customer(
            db, customer["id"], "🔐 Bagong Device Login",
            "May bagong device/browser na nag-login sa account mo gamit ang QR code mo. Kung hindi ikaw ito, sabihin agad sa amin.",
            url=f"/customer/{customer['id']}/dashboard",
        )

    flash(f"Welcome, {customer.get('name')}!", "success")
    resp = make_response(redirect(url_for("customer_portal.dashboard_page", customer_id=customer["id"])))
    customer_auth.set_device_cookie(resp, device_id)
    return resp


@customer_portal_bp.route("/customer/logout")
def logout_page():
    session.pop("customer_id", None)
    session.pop("customer_name", None)
    flash("Na-logout ka na.", "success")
    return redirect(url_for("customer_portal.login_page"))


@customer_portal_bp.route("/customer/<customer_id>/dashboard")
def dashboard_page(customer_id):
    if not session.get("customer_id") and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.login_page"))
    if session.get("customer_id") and session.get("customer_id") != customer_id and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.dashboard_page", customer_id=session["customer_id"]))
    customer = get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customer_portal.login_page"))
    return render_template(
        "customer_dashboard.html",
        customer=customer,
        vapid_public_key=push_notify.VAPID_PUBLIC_KEY,
        push_enabled=push_notify.PUSH_ENABLED,
    )


@customer_portal_bp.route("/customer/<customer_id>/order")
def order_page(customer_id):
    if not session.get("customer_id") and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.login_page"))
    if session.get("customer_id") and session.get("customer_id") != customer_id and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.order_page", customer_id=session["customer_id"]))
    customer = get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customer_portal.login_page"))
    return render_template("customer_order.html", customer=customer, container_types=list_container_types())


@customer_portal_bp.route("/customer/<customer_id>/history")
def history_page(customer_id):
    if not session.get("customer_id") and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.login_page"))
    if session.get("customer_id") and session.get("customer_id") != customer_id and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.history_page", customer_id=session["customer_id"]))
    customer = get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customer_portal.login_page"))
    orders = [o for o in list_orders() if o.get("customer_id") == customer_id]
    loyalty_log = loyalty.get_loyalty_log(customer_id, limit=30)
    for entry in loyalty_log:
        ts = loyalty.parse_ts(entry.get("timestamp"))
        entry["display_date"] = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y") if ts else ""
        )
    return render_template("customer_history.html", customer=customer, orders=orders, loyalty_log=loyalty_log)


@customer_portal_bp.route("/customer/<customer_id>/chat")
def chat_page(customer_id):
    """MODULE 9 (cont'd): lets a customer chat directly with the owner
    about their order - reuses the EXACT SAME chat_threads/chat_messages
    backend as the staff-side Chats page (models/chats.py), so a message
    sent here shows up immediately for staff at /chats/<thread_id>, and
    vice versa. Every customer already has a chat_thread_id from the
    moment their account was created (see models/customers.py
    create_customer())."""
    if not session.get("customer_id") and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.login_page"))
    if session.get("customer_id") and session.get("customer_id") != customer_id and not _is_owner_or_staff():
        return redirect(url_for("customer_portal.chat_page", customer_id=session["customer_id"]))
    customer = get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customer_portal.login_page"))

    thread_id = customer.get("chat_thread_id")
    messages = chats_model.list_messages(thread_id) if thread_id else []
    if thread_id and session.get("customer_id") == customer_id:
        chats_model.mark_thread_seen_by_customer(thread_id)
    return render_template("customer_chat.html", customer=customer, messages=messages)


@customer_portal_bp.route("/api/customer/<customer_id>/chat/send", methods=["POST"])
def api_chat_send(customer_id):
    if not _can_access(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    customer = get_customer(customer_id)
    thread_id = customer.get("chat_thread_id") if customer else None
    if not thread_id:
        return jsonify({"ok": False, "error": "Walang chat thread para sa account na ito."}), 400

    text = request.form.get("message_text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Wala kang na-type na mensahe."}), 400

    chats_model.send_message(thread_id, "customer", customer_id, message_text=text, message_type="text")
    push_notify.send_push_to_owner(
        db, f"💬 {customer.get('name')}",
        text[:120],
        url=f"/chats/{thread_id}",
    )
    return jsonify({"ok": True})


@customer_portal_bp.route("/api/customer/<customer_id>/chat/poll")
def api_chat_poll(customer_id):
    """Polling endpoint (same lightweight approach as chats.py's
    poll_messages()) so the customer's chat page updates without a full
    page reload, with no extra Firebase web SDK config needed."""
    if not _can_access(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    customer = get_customer(customer_id)
    thread_id = customer.get("chat_thread_id") if customer else None
    if not thread_id:
        return jsonify({"ok": True, "messages": []})
    messages = chats_model.list_messages(thread_id)
    return jsonify({
        "ok": True,
        "messages": [
            {
                "id": m["id"],
                "sender_type": m["sender_type"],
                "message_text": m.get("message_text", ""),
                "message_type": m.get("message_type", "text"),
                "image_url": m.get("image_url"),
            }
            for m in messages
        ],
    })


# ---------------------------------------------------------------------------
# Auth APIs
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/api/customer/login", methods=["POST"])
def api_login():
    data = request.form
    phone = data.get("phone", "")
    password = data.get("password", "")
    if not phone or not password:
        flash("Kailangan ang phone number at password.", "warning")
        return redirect(url_for("customer_portal.login_page"))

    customer, error = customer_auth.verify_login(phone, password)
    if not customer:
        flash(error, "danger")
        return redirect(url_for("customer_portal.login_page"))

    # Clear any leftover owner/staff identity - a customer session must
    # never carry both at once (see module docstring).
    session.pop("user_id", None)
    session.pop("user_name", None)
    session.pop("user_role", None)
    session.pop("rider_id", None)
    session["customer_id"] = customer["id"]
    session["customer_name"] = customer.get("name")

    is_new_device, device_id = customer_auth.check_and_register_device(customer["id"])
    if is_new_device:
        push_notify.send_push_to_customer(
            db, customer["id"], "🔐 Bagong Device Login",
            "May bagong device/browser na nag-login sa account mo. Kung hindi ikaw ito, palitan agad ang password mo.",
            url=f"/customer/{customer['id']}/dashboard",
        )

    flash(f"Welcome, {customer.get('name')}!", "success")
    resp = make_response(redirect(url_for("customer_portal.dashboard_page", customer_id=customer["id"])))
    customer_auth.set_device_cookie(resp, device_id)
    return resp


@customer_portal_bp.route("/api/customer/request_otp", methods=["POST"])
def api_request_otp():
    phone = request.form.get("phone", "")
    ok, result = customer_auth.request_otp(phone)
    if not ok:
        flash(result, "danger")
        return redirect(url_for("customer_portal.login_page"))
    # No SMS gateway configured - shown directly (see models/customer_auth.py
    # module docstring for why). Wire in an SMS provider here later if the
    # phone numbers ever need to stay private from whoever's holding the phone.
    flash(f"Ang OTP code mo (5 minuto lang bago mag-expire): {result}", "success")
    return redirect(url_for("customer_portal.login_page"))


@customer_portal_bp.route("/api/customer/verify_otp", methods=["POST"])
def api_verify_otp():
    phone = request.form.get("phone", "")
    otp = request.form.get("otp", "")
    new_password = request.form.get("new_password", "")
    ok, error = customer_auth.verify_otp_and_reset_password(phone, otp, new_password)
    if not ok:
        flash(error, "danger")
    else:
        flash("Na-reset na ang password mo. Mag-login ka na gamit ang bago.", "success")
    return redirect(url_for("customer_portal.login_page"))


@customer_portal_bp.route("/api/customer/<customer_id>/change_password", methods=["POST"])
@customer_auth.customer_login_required
def api_change_password(customer_id):
    if session.get("customer_id") != customer_id:
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    ok, error = customer_auth.change_password(customer_id, current, new)
    if not ok:
        flash(error, "danger")
    else:
        flash("Napalitan na ang password mo.", "success")
    return redirect(url_for("customer_portal.dashboard_page", customer_id=customer_id))


# ---------------------------------------------------------------------------
# Ordering APIs
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/api/customer/<customer_id>/place_order", methods=["POST"])
def api_place_order(customer_id):
    if not _can_access(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    try:
        qty = int(request.form.get("containers_qty", 0))
    except ValueError:
        qty = 0
    delivery_date = request.form.get("delivery_date", "")
    container_type = request.form.get("container_type", DEFAULT_CONTAINER_TYPE)

    if qty <= 0:
        flash("Invalid na quantity ng containers.", "danger")
        return redirect(url_for("customer_portal.order_page", customer_id=customer_id))
    if not get_container_type(container_type):
        flash("Invalid na container type.", "danger")
        return redirect(url_for("customer_portal.order_page", customer_id=customer_id))

    order = create_order(customer_id, qty, delivery_date=delivery_date, container_type=container_type)

    # MODULE 9: auto-apply any loyalty free-gallon credits the customer
    # already has (e.g. from completing a 100-gallon card) - discount is
    # computed at THIS order's own per-gallon rate, so it's fair whatever
    # container type they ordered.
    free_gallons, discount, billable = loyalty.apply_free_gallons(
        customer_id, order["gallons_total"], order["amount_due"], order_id=order["id"])
    if free_gallons > 0:
        db.collection("orders").document(order["id"]).update({
            "amount_due": billable,
            "free_gallons_applied": free_gallons,
            "loyalty_discount_amount": discount,
        })
        order["amount_due"] = billable

    push_notify.send_push_to_owner(
        db, "💧 Bagong Order!",
        f"{order.get('customer_name')} - {qty}x {order['container_label']}" + (f" ({delivery_date})" if delivery_date else ""),
        url="/orders",
    )
    if session.get("customer_id") == customer_id:
        customer_auth.log_activity(customer_id, order.get("customer_name"), "Nag-order",
                                    f"{qty}x {order['container_label']} ({delivery_date or 'walang petsa'})")
    if free_gallons > 0:
        flash(f"Na-place ang order mo: {qty}x {order['container_label']} (₱{discount:.2f} libre mula sa loyalty card mo). Salamat!", "success")
    else:
        flash(f"Na-place ang order mo: {qty}x {order['container_label']}. Salamat!", "success")
    return redirect(url_for("customer_portal.history_page", customer_id=customer_id))


# ---------------------------------------------------------------------------
# Loyalty card API (MODULE 9 - matches the printed punch card exactly: 10
# containers = 1 free container, auto-applied on the customer's next order,
# no manual "redeem" step needed)
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/api/customer/<customer_id>/loyalty_card")
def api_loyalty_card(customer_id):
    if not _can_access(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    card = loyalty.get_loyalty_card(customer_id)
    return jsonify({"ok": True, **card})


# ---------------------------------------------------------------------------
# Web push subscribe/unsubscribe (customer's own device)
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/api/customer/push/subscribe", methods=["POST"])
def api_push_subscribe():
    if not session.get("customer_id"):
        return jsonify({"ok": False, "error": "Login required"}), 401
    data = request.json or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"ok": False, "error": "Invalid subscription"}), 400
    sub_id = push_notify.subscription_id(endpoint)
    db.collection("push_subscriptions").document(sub_id).set({
        "endpoint": endpoint,
        "keys": {"p256dh": keys.get("p256dh"), "auth": keys.get("auth")},
        "customer_id": session["customer_id"],
        "role": "customer",
    })
    return jsonify({"ok": True})


@customer_portal_bp.route("/api/customer/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    data = request.json or {}
    endpoint = data.get("endpoint")
    if endpoint:
        db.collection("push_subscriptions").document(push_notify.subscription_id(endpoint)).delete()
    return jsonify({"ok": True})
