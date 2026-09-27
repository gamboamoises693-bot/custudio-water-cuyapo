"""MODULE 3 (rider side): Rider App page - mark orders as delivered + upload photo proof.
MODULE 6 support: writes feed rider_performance_today() via models/orders.py -> models/riders.py.
"""

import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from auth import login_required, role_required
from models import orders as orders_model
from models import riders as riders_model
from firebase_config import bucket, USING_MOCK_DB

deliveries_bp = Blueprint("deliveries", __name__, url_prefix="/deliveries")

ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


def _allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def _upload_proof_photo(file_storage):
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"

    if USING_MOCK_DB:
        upload_dir = os.path.join("static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        file_storage.save(os.path.join(upload_dir, filename))
        return f"/static/uploads/{filename}"

    blob = bucket.blob(f"delivery_proofs/{filename}")
    blob.upload_from_file(file_storage.stream, content_type=file_storage.content_type)
    blob.make_public()
    return blob.public_url


@deliveries_bp.route("/rider")
@login_required
@role_required("rider", "owner", "staff")
def rider_app():
    """Rider sees only their own 'on_delivery' orders (per spec: 'kita nya lang orders
    nya na on_delivery'). Owner/staff can view any rider by ?rider_id= for oversight."""
    rider_id = session.get("rider_id") or request.args.get("rider_id")
    riders = riders_model.list_riders()

    if not rider_id and riders:
        rider_id = riders[0]["id"]

    my_orders = orders_model.list_orders(status="on_delivery", rider_id=rider_id) if rider_id else []
    current_rider = riders_model.get_rider(rider_id) if rider_id else None

    return render_template(
        "rider_app.html",
        orders=my_orders,
        riders=riders,
        current_rider=current_rider,
        selected_rider_id=rider_id,
    )


@deliveries_bp.route("/<order_id>/mark", methods=["POST"])
@login_required
@role_required("rider", "owner", "staff")
def mark_delivered(order_id):
    order = orders_model.get_order(order_id)
    if not order:
        flash("Wala nang ganitong order.", "danger")
        return redirect(url_for("deliveries.rider_app"))

    rider_id = order.get("rider_id") or session.get("rider_id")
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

    orders_model.mark_delivered(order_id, rider_id, qty, payment_type, amount, photo_url)
    flash("Na-mark as delivered! Na-notify na ang customer sa chat.", "success")
    return redirect(url_for("deliveries.rider_app", rider_id=rider_id))
