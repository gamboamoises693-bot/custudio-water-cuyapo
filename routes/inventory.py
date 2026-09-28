"""MODULE 4: Inventory & Tank Monitor."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required
from models import inventory as inventory_model

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")


@inventory_bp.route("/")
@login_required
def list_view():
    tanks = inventory_model.list_tanks()
    items = inventory_model.list_items()
    low_tank_alerts = inventory_model.get_low_tank_alerts()
    low_stock_items = inventory_model.get_low_stock_items()
    return render_template(
        "inventory.html",
        tanks=tanks,
        items=items,
        low_tank_alerts=low_tank_alerts,
        low_stock_items=low_stock_items,
    )


@inventory_bp.route("/tanks/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def create_tank():
    tank_type = request.form.get("tank_type", "raw")
    level = request.form.get("level_percent", "0")
    try:
        level_val = float(level)
    except ValueError:
        level_val = 0.0
    inventory_model.create_tank(tank_type, level_val)
    flash("Nadagdag ang tank.", "success")
    return redirect(url_for("inventory.list_view"))


@inventory_bp.route("/tanks/<tank_id>/update", methods=["POST"])
@login_required
@role_required("owner", "staff")
def update_tank(tank_id):
    level = request.form.get("level_percent", "0")
    try:
        level_val = float(level)
    except ValueError:
        level_val = 0.0
    inventory_model.update_tank_level(tank_id, level_val)
    flash("Na-update ang tank level.", "success")
    return redirect(url_for("inventory.list_view"))


@inventory_bp.route("/items/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def create_item():
    item_name = request.form.get("item_name", "").strip()
    stock_qty = request.form.get("stock_qty", "0")
    low_alert_qty = request.form.get("low_alert_qty", "0")
    try:
        stock_val = int(stock_qty)
        low_val = int(low_alert_qty)
    except ValueError:
        stock_val, low_val = 0, 0

    if not item_name:
        flash("Kailangan ng item name.", "danger")
        return redirect(url_for("inventory.list_view"))

    inventory_model.create_item(item_name, stock_val, low_val)
    flash("Nadagdag ang inventory item.", "success")
    return redirect(url_for("inventory.list_view"))


@inventory_bp.route("/items/<item_id>/update", methods=["POST"])
@login_required
@role_required("owner", "staff")
def update_item(item_id):
    stock_qty = request.form.get("stock_qty", "0")
    try:
        stock_val = int(stock_qty)
    except ValueError:
        stock_val = 0
    inventory_model.update_item_stock(item_id, stock_val)
    flash("Na-update ang stock.", "success")
    return redirect(url_for("inventory.list_view"))


@inventory_bp.route("/items/<item_id>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete_item(item_id):
    inventory_model.delete_item(item_id)
    flash("Na-delete ang item.", "success")
    return redirect(url_for("inventory.list_view"))
