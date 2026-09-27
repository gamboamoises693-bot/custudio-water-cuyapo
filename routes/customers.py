"""MODULE 1: Customer Management + Chat Profile."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required
from models import customers as customers_model
from models import chats as chats_model

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

BARANGAYS_CUYAPO = [
    "District 1", "District 2", "District 3", "District 4",
    "Poblacion", "Sto. Domingo", "San Juan", "Villa Fronda",
]


@customers_bp.route("/")
@login_required
def list_view():
    barangay = request.args.get("barangay") or None
    search = request.args.get("q") or None
    customers = customers_model.list_customers(barangay=barangay, search=search)

    # attach chat preview (last_message) for the "Messenger contacts" style list
    threads_by_id = {t["id"]: t for t in chats_model.list_threads()}
    for c in customers:
        thread = threads_by_id.get(c.get("chat_thread_id"))
        c["last_message"] = thread.get("last_message", "") if thread else ""
        c["unread_count_owner"] = thread.get("unread_count_owner", 0) if thread else 0

    return render_template(
        "customers.html",
        customers=customers,
        barangays=BARANGAYS_CUYAPO,
        selected_barangay=barangay,
        search=search or "",
    )


@customers_bp.route("/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def create():
    name = request.form.get("name", "").strip()
    barangay = request.form.get("barangay", "").strip()
    phone = request.form.get("phone", "").strip()
    price = request.form.get("price_per_container", "0").strip()
    cust_type = request.form.get("type", "residential")

    if not name or not barangay or not phone:
        flash("Kailangan lahat ng fields (name, barangay, phone).", "danger")
        return redirect(url_for("customers.list_view"))

    try:
        price_val = float(price)
    except ValueError:
        price_val = 0.0

    customers_model.create_customer(name, barangay, phone, price_val, cust_type)
    flash(f"Nadagdag si {name} sa customers.", "success")
    return redirect(url_for("customers.list_view"))


@customers_bp.route("/<customer_id>/edit", methods=["POST"])
@login_required
@role_required("owner", "staff")
def edit(customer_id):
    updates = {}
    for field in ("name", "barangay", "phone", "type"):
        val = request.form.get(field)
        if val:
            updates[field] = val.strip()
    price = request.form.get("price_per_container")
    if price:
        try:
            updates["price_per_container"] = float(price)
        except ValueError:
            pass

    customers_model.update_customer(customer_id, updates)
    flash("Na-update ang customer.", "success")
    return redirect(url_for("customers.list_view"))


@customers_bp.route("/<customer_id>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete(customer_id):
    customers_model.delete_customer(customer_id)
    flash("Na-delete ang customer.", "success")
    return redirect(url_for("customers.list_view"))
