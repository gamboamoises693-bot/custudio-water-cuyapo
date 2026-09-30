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

import os
import re
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, make_response, current_app

from firebase_config import db
from models import customer_auth
from models import loyalty
from models import chats as chats_model
from models.customers import get_customer, get_customer_by_qr_token
from models.orders import get_order, list_orders, create_order, confirm_delivery
from models.activity import record_action, bump_engagement, bump_video_view
from models.pricing import list_container_types, get_container_type, DEFAULT_CONTAINER_TYPE
from models.timeutil import format_dt
import push_notify

customer_portal_bp = Blueprint("customer_portal", __name__)


def _is_owner_or_staff():
    return bool(session.get("user_id"))


def _can_access(customer_id):
    if session.get("customer_id") == customer_id:
        return True
    return _is_owner_or_staff()


_VIDEO_EXTS = ("mp4", "webm", "mov", "m4v")


def _list_promo_videos():
    """Returns the full promo-video playlist: [{filename, title, url,
    views, source, ...}, ...], ordered per Isesmo's saved preference (see
    ORDERING below) - entry [0] is always what auto-plays by default on
    both the Customer Login page and Customer Dashboard.

    TWO SOURCES, merged into one list:
    1. "file" - video files sitting under static/videos/, added the
       original way (GitHub web upload, no code change needed). A display
       title is derived from the filename (e.g. "bagong-promo_2.mp4" ->
       "Bagong Promo 2") so a reasonably-named file already looks fine in
       the picker without extra typing.
    2. "upload" - videos Isesmo uploaded DIRECTLY from the Video Playlist
       admin page (routes/customers.py's video_playlist_upload()), stored
       in Firebase Storage with a small Firestore record (see
       models/promo_videos.py - and its module docstring for why these
       are NOT saved to static/videos/ on the server instead).
    Both keep working side by side - nothing uploaded the old way stops
    working just because the new upload button exists.

    ORDERING: Isesmo can pin/reorder videos (from either source) on the
    Video Playlist admin page, which saves a plain list of filenames via
    models.activity.save_video_order(). Files that appear in that saved
    order come first, in that exact order (so "pinning" a video = moving
    it to position 0 there). Any video he hasn't arranged yet (e.g. a
    brand-new upload) falls in AFTER those, sorted alphabetically, so a
    new video never silently disappears from the picker just because it's
    not in the saved order yet.

    Returns [] if there are no videos from either source - templates use
    this to hide the whole video section, same self-hiding behavior as
    before."""
    found = {}

    videos_dir = os.path.join(current_app.static_folder, "videos")
    if os.path.isdir(videos_dir):
        for fname in sorted(os.listdir(videos_dir)):
            if "." not in fname:
                continue
            ext = fname.rsplit(".", 1)[1].lower()
            if ext not in _VIDEO_EXTS:
                continue
            stem = fname.rsplit(".", 1)[0]
            title = re.sub(r"[-_]+", " ", stem).strip().title() or "Video"
            found[fname] = {
                "filename": fname,
                "title": title,
                "url": url_for("static", filename=f"videos/{fname}"),
                "source": "file",
            }

    from models.promo_videos import list_promo_video_docs
    for doc in list_promo_video_docs():
        fname = doc.get("filename")
        if not fname:
            continue
        found[fname] = {
            "filename": fname,
            "title": doc.get("title") or "Video",
            "url": doc.get("url"),
            "source": "upload",
            "doc_id": doc.get("id"),
        }

    if not found:
        return []

    from models.activity import get_video_order, get_video_views
    views = get_video_views()
    for info in found.values():
        info["views"] = views.get(info["filename"], 0)

    saved_order = get_video_order()
    out = [found.pop(fname) for fname in saved_order if fname in found]
    out.extend(found[fname] for fname in sorted(found))
    return out


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@customer_portal_bp.route("/customer")
def login_page():
    if session.get("customer_id"):
        return redirect(url_for("customer_portal.dashboard_page", customer_id=session["customer_id"]))
    return render_template("customer_login.html", promo_videos=_list_promo_videos())


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

    # (No "Welcome, {name}!" flash here - it duplicated the dashboard's own
    # "Kumusta, {{ customer.name }}!" header greeting right below it.)
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
        promo_videos=_list_promo_videos(),
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
        # Manila-local, not UTC - see models/timeutil.py's module docstring.
        entry["display_date"] = format_dt(entry.get("timestamp"), "%b %d, %Y")
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
        # Customer is actually looking at the thread now, so flip every
        # owner/staff message to seen=True - lets the owner's Chats page
        # show "Nakita na" (seen) on their last sent message.
        chats_model.mark_messages_seen_by_customer(thread_id)
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
    page reload, with no extra Firebase web SDK config needed. Every poll
    also marks the owner's messages as seen=True (the customer's screen is
    open and actively refreshing, so this is the most accurate "did they
    see it" signal we have) and carries a pre-formatted Manila-local
    `time_label` per message."""
    from models.timeutil import format_time

    if not _can_access(customer_id):
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    customer = get_customer(customer_id)
    thread_id = customer.get("chat_thread_id") if customer else None
    if not thread_id:
        return jsonify({"ok": True, "messages": []})
    chats_model.mark_messages_seen_by_customer(thread_id)
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
                "time_label": format_time(m.get("timestamp")),
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

    # (No "Welcome, {name}!" flash here either - see qr_login() above for why.)
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


@customer_portal_bp.route("/api/customer/<customer_id>/orders/<order_id>/confirm", methods=["POST"])
def api_confirm_order(customer_id, order_id):
    """Customer taps "Kumpirmahin ang Pagkatanggap" on their Order History
    page for an order staff already marked delivered/paid/utang. THIS is
    now what triggers their loyalty gallons - see models/orders.py's
    confirm_delivery() and models/loyalty.py's module docstring."""
    if session.get("customer_id") != customer_id:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    try:
        order = confirm_delivery(order_id, customer_id)
    except ValueError as e:
        flash(str(e), "danger")
    else:
        record_action(
            "Order Confirmed ng Customer", f"{order.get('customer_name')} - {order.get('containers_qty')}x {order.get('container_label')}",
            actor_name=order.get("customer_name"),
        )
        flash("Salamat sa pag-confirm! Na-apply na ang gallons mo sa loyalty card.", "success")
    return redirect(url_for("customer_portal.history_page", customer_id=customer_id))


@customer_portal_bp.route("/api/track/video-play", methods=["POST"])
def api_track_video_play():
    """Counts a play of the promo video shown on the Customer Login page
    and Customer Dashboard - fired by the video's own 'play' event (see
    those templates' inline scripts). No login needed since the video is
    shown before login too. Public/best-effort - a small over-count from a
    replay or seek is fine, this is a rough engagement number, not a
    billing figure.

    `filename` (optional, sent by the templates' JS) identifies WHICH video
    was played, on top of the overall video_plays total - lets Isesmo see
    per-video view counts on the Video Playlist admin page. Missing/blank
    filename (e.g. an old cached page from before this was added) just
    skips the per-video bump, the overall total still counts."""
    bump_engagement("video_plays")
    filename = request.form.get("filename", "").strip()
    if filename:
        bump_video_view(filename)
    return jsonify({"ok": True})


@customer_portal_bp.route("/api/track/omega-click", methods=["POST"])
def api_track_omega_click():
    """Counts a tap of the OMEGA PURIFIED ICE cross-promo banner link -
    same public/best-effort engagement counter as video plays above."""
    bump_engagement("omega_link_clicks")
    return jsonify({"ok": True})


@customer_portal_bp.route("/api/track/omega-fb-click", methods=["POST"])
def api_track_omega_fb_click():
    """Counts a tap of the OMEGA PURIFIED ICE banner's Facebook Page link
    (per owner's request, "maglagay ng page link sa banner ... dederesto sa
    omega page message") - separate counter from the main
    omega_link_clicks (which is for the Omega Ice ordering portal link),
    so Isesmo can see which of the two CTAs customers actually use more."""
    bump_engagement("omega_fb_clicks")
    return jsonify({"ok": True})


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
    record_action(
        "Bagong Order (Customer Portal)",
        f"{order.get('customer_name')} - {qty}x {order['container_label']}" + (f" ({delivery_date})" if delivery_date else ""),
        actor_name=order.get("customer_name"),
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
