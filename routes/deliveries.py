"""MODULE 3 (delivery side): Deliveries page - staff marks 'on_delivery'
orders as delivered + upload photo proof + collect payment.

No named/tracked riders in-system anymore - the branch uses its own
external delivery riders, so this page just lists every order that's
currently 'on_delivery' (see models/orders.py module docstring for the
full status lifecycle) for whichever staff member is processing deliveries.
"""

import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from auth import login_required, role_required, current_user, owner_or_super_admin_required
from models import orders as orders_model
from models.activity import record_action
from firebase_config import bucket, USING_MOCK_DB

deliveries_bp = Blueprint("deliveries", __name__, url_prefix="/deliveries")

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


def _allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def _upload_proof_photo(file_storage):
    """Saves the delivery proof photo to Firebase Storage when it's actually
    usable, otherwise falls back to local disk (static/uploads) - covers both
    mock/demo mode and a real Firebase project that's still on the free
    (Spark) plan, which no longer includes Storage.
    """
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"

    if not USING_MOCK_DB and bucket is not None:
        try:
            blob = bucket.blob(f"delivery_proofs/{filename}")
            blob.upload_from_file(file_storage.stream, content_type=file_storage.content_type)
            blob.make_public()
            return blob.public_url
        except Exception as e:
            print(f"[deliveries] Firebase Storage upload failed ({e}); saving locally instead.")
            file_storage.stream.seek(0)

    upload_dir = os.path.join("static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, filename))
    return f"/static/uploads/{filename}"


@deliveries_bp.route("/rider")
@login_required
@role_required("owner", "staff")
def rider_app():
    """Lists every order that's currently 'on_delivery', for whichever
    staff member is out processing/confirming deliveries today."""
    orders = orders_model.list_orders(status="on_delivery")
    return render_template("rider_app.html", orders=orders)


@deliveries_bp.route("/<order_id>/mark", methods=["POST"])
@login_required
@role_required("owner", "staff")
def mark_delivered(order_id):
    order = orders_model.get_order(order_id)
    if not order:
        flash("Wala nang ganitong order.", "danger")
        return redirect(url_for("deliveries.rider_app"))

    delivered_qty = request.form.get("delivered_qty", "0")
    payment_type = request.form.get("payment_type", "cash")
    amount_collected = request.form.get("amount_collected", "0")
    photo = request.files.get("photo_proof")

    try:
        qty = int(delivered_qty)
        amount = float(amount_collected)
    except ValueError:
        flash("Invalid na quantity o amount.", "danger")
        return redirect(url_for("deliveries.rider_app"))

    photo_url = None
    if photo and photo.filename and _allowed_image(photo.filename):
        photo_url = _upload_proof_photo(photo)

    delivered_by = (current_user() or {}).get("name")
    orders_model.mark_delivered(order_id, qty, payment_type, amount, photo_url, delivered_by=delivered_by)
    record_action("Order Delivered", f"{order.get('customer_name')} - {qty}x {order.get('container_label')} - ₱{amount:.2f} ({payment_type})")
    flash("Na-mark as delivered! Na-notify na ang customer sa chat.", "success")
    return redirect(url_for("deliveries.rider_app"))


@deliveries_bp.route("/<order_id>/delete", methods=["POST"])
@login_required
@owner_or_super_admin_required
def delete(order_id):
    """Cancels/removes an order straight from the Deliveries queue - Owner
    (e.g. Cindy) or super-admin (Isesmo) only (see auth.py's
    owner_or_super_admin_required)."""
    order = orders_model.get_order(order_id)
    orders_model.delete_order(order_id)
    if order:
        record_action("Delivery Deleted", f"{order.get('customer_name')} - {order.get('containers_qty')}x {order.get('container_label')}")
    flash("Na-delete ang order/delivery.", "success")
    return redirect(url_for("deliveries.rider_app"))
