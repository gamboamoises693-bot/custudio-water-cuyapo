"""MODULE 2: Personal Chat per Customer (main feature) + MODULE 8: Chat Broadcast."""

import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from auth import login_required
from models import chats as chats_model
from models import customers as customers_model
from models import orders as orders_model
from firebase_config import bucket, USING_MOCK_DB

chats_bp = Blueprint("chats", __name__, url_prefix="/chats")

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


def _allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def _upload_chat_image(file_storage):
    """Uploads an image (GCash receipt / delivery photo) to Firebase Storage and
    returns its public URL. In mock/demo mode (no real Firebase Storage configured),
    saves it locally under static/uploads instead so the demo still works end-to-end.
    """
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"

    if USING_MOCK_DB:
        upload_dir = os.path.join("static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        local_path = os.path.join(upload_dir, filename)
        file_storage.save(local_path)
        return f"/static/uploads/{filename}"

    blob = bucket.blob(f"chat_images/{filename}")
    blob.upload_from_file(file_storage.stream, content_type=file_storage.content_type)
    blob.make_public()
    return blob.public_url


@chats_bp.route("/")
@login_required
def list_view():
    threads = chats_model.list_threads()
    return render_template("chat.html", threads=threads, active_thread=None, messages=[], quick_replies=chats_model.QUICK_REPLIES)


@chats_bp.route("/<thread_id>")
@login_required
def view_thread(thread_id):
    threads = chats_model.list_threads()
    thread = chats_model.get_thread(thread_id)
    if not thread:
        flash("Wala nang ganitong chat thread.", "danger")
        return redirect(url_for("chats.list_view"))

    messages = chats_model.list_messages(thread_id)
    chats_model.mark_thread_seen_by_owner(thread_id)
    customer = customers_model.get_customer(thread["customer_id"])

    return render_template(
        "chat.html",
        threads=threads,
        active_thread=thread,
        messages=messages,
        customer=customer,
        quick_replies=chats_model.QUICK_REPLIES,
    )


@chats_bp.route("/<thread_id>/send", methods=["POST"])
@login_required
def send(thread_id):
    text = request.form.get("message_text", "").strip()
    image = request.files.get("image")
    sender_type = "owner" if session.get("user_role") == "owner" else "staff"
    sender_id = session.get("user_id")

    if image and image.filename and _allowed_image(image.filename):
        image_url = _upload_chat_image(image)
        chats_model.send_message(thread_id, sender_type, sender_id, message_type="image", image_url=image_url)

    if text:
        chats_model.send_message(thread_id, sender_type, sender_id, message_text=text, message_type="text")

    if not text and not (image and image.filename):
        flash("Wala kang na-type na mensahe o na-attach na image.", "warning")

    return redirect(url_for("chats.view_thread", thread_id=thread_id))


@chats_bp.route("/<thread_id>/create-order", methods=["POST"])
@login_required
def create_order_from_chat(thread_id):
    containers_qty = request.form.get("containers_qty", "0")
    delivery_date = request.form.get("delivery_date", "")
    try:
        qty = int(containers_qty)
    except ValueError:
        qty = 0

    if qty <= 0:
        flash("Invalid na quantity ng containers.", "danger")
        return redirect(url_for("chats.view_thread", thread_id=thread_id))

    orders_model.create_order_from_chat(thread_id, qty, delivery_date)
    flash(f"Nagawa ang order: {qty} container/s.", "success")
    return redirect(url_for("chats.view_thread", thread_id=thread_id))


@chats_bp.route("/<thread_id>/confirm-payment", methods=["POST"])
@login_required
def confirm_payment(thread_id):
    order_id = request.form.get("order_id")
    if not order_id:
        flash("Walang order na napili para i-confirm.", "danger")
        return redirect(url_for("chats.view_thread", thread_id=thread_id))
    orders_model.confirm_chat_payment(order_id, thread_id)
    flash("Na-confirm ang payment.", "success")
    return redirect(url_for("chats.view_thread", thread_id=thread_id))


@chats_bp.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if not message:
            flash("Wala kang na-type na broadcast message.", "warning")
            return redirect(url_for("chats.broadcast"))

        threads = chats_model.list_threads()
        sender_id = session.get("user_id")
        for t in threads:
            chats_model.send_message(t["id"], "owner", sender_id, message_text=message, message_type="text")

        flash(f"Na-send ang broadcast sa {len(threads)} customer/s (personal message pa rin kada thread).", "success")
        return redirect(url_for("chats.broadcast"))

    return render_template("broadcast.html")


@chats_bp.route("/<thread_id>/poll")
@login_required
def poll_messages(thread_id):
    """Lightweight JSON polling endpoint used by chat.js as a Firestore-onSnapshot-style
    live update fallback (works even without exposing Firebase web SDK config)."""
    messages = chats_model.list_messages(thread_id)
    return jsonify({
        "messages": [
            {
                "id": m["id"],
                "sender_type": m["sender_type"],
                "message_text": m.get("message_text", ""),
                "message_type": m.get("message_type", "text"),
                "image_url": m.get("image_url"),
            }
            for m in messages
        ]
    })
