"""MODULE 3: Order & Delivery (creation/accept/start-delivery side; delivery
marking (payment collection + photo proof) lives in routes/deliveries.py)."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required, super_admin_required
from models import orders as orders_model
from models import customers as customers_model
from models.pricing import list_container_types, get_container_type, DEFAULT_CONTAINER_TYPE

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")


@orders_bp.route("/")
@login_required
def list_view():
    status_filter = request.args.get("status") or None
    orders = orders_model.list_orders(status=status_filter)
    customers = customers_model.list_customers()
    return render_template(
        "orders.html",
        orders=orders,
        customers=customers,
        status_filter=status_filter,
        statuses=orders_model.VALID_STATUSES,
        container_types=list_container_types(),
    )


@orders_bp.route("/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def create():
    customer_id = request.form.get("customer_id")
    containers_qty = request.form.get("containers_qty", "0")
    delivery_date = request.form.get("delivery_date", "")
    container_type = request.form.get("container_type", DEFAULT_CONTAINER_TYPE)

    try:
        qty = int(containers_qty)
    except ValueError:
        qty = 0

    if not customer_id or qty <= 0:
        flash("Pumili ng customer at valid na quantity.", "danger")
        return redirect(url_for("orders.list_view"))
    if not get_container_type(container_type):
        flash("Invalid na container type.", "danger")
        return redirect(url_for("orders.list_view"))

    try:
        orders_model.create_order(customer_id, qty, delivery_date, container_type=container_type)
    except ValueError:
        flash("Customer not found.", "danger")
        return redirect(url_for("orders.list_view"))
    flash("Nagawa ang bagong order.", "success")
    return redirect(url_for("orders.list_view"))


@orders_bp.route("/<order_id>/accept", methods=["POST"])
@login_required
@role_required("owner", "staff")
def accept(order_id):
    orders_model.accept_order(order_id)
    flash("Na-accept na ang order.", "success")
    return redirect(url_for("orders.list_view"))


@orders_bp.route("/<order_id>/start-delivery", methods=["POST"])
@login_required
@role_required("owner", "staff")
def start_delivery(order_id):
    orders_model.start_delivery(order_id)
    flash("Padala na sa delivery! Makikita na ito sa Deliveries page.", "success")
    return redirect(url_for("orders.list_view"))


@orders_bp.route("/<order_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def delete(order_id):
    """Hard delete - locked to gamboamoises693@gmail.com only (see auth.py's
    super_admin_required). Removes the order + any linked delivery record."""
    orders_model.delete_order(order_id)
    flash("Na-delete ang order.", "success")
    return redirect(url_for("orders.list_view"))
