"""MODULE 1: Customer Management + Chat Profile."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required
from models import customers as customers_model
from models import chats as chats_model
from models import customer_auth

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
    cust_type = request.form.get("type", "residential")

    if not name or not barangay or not phone:
        flash("Kailangan lahat ng fields (name, barangay, phone).", "danger")
        return redirect(url_for("customers.list_view"))

    customers_model.create_customer(name, barangay, phone, cust_type)
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


@customers_bp.route("/<customer_id>/set-password", methods=["POST"])
@login_required
@role_required("owner", "staff")
def set_password(customer_id):
    """MODULE 9: gives a customer their initial Customer Portal login (or
    resets it if they forgot it and can't use the in-app OTP flow, e.g. a
    non-smartphone customer calling in). The customer's PHONE NUMBER on file
    is their portal username - this only sets the password half."""
    new_password = request.form.get("new_password", "").strip()
    if len(new_password) < 4:
        flash("Kailangan ng minimum 4 characters ang password.", "danger")
        return redirect(url_for("customers.list_view"))

    customer = customers_model.get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.list_view"))

    customers_model.update_customer(customer_id, {"password_hash": customer_auth.hash_password(new_password)})
    flash(f"Na-set ang Customer Portal password ni {customer.get('name')}. Sabihin sa kanya: phone number nya + password na ito ang gagamitin sa /customer.", "success")
    return redirect(url_for("customers.list_view"))


@customers_bp.route("/activity")
@login_required
@role_required("owner", "staff")
def activity_log():
    """MODULE 9: audit trail of Customer Portal logins (success + failed)
    and what logged-in customers did (ordered, redeemed, etc.) - the
    self-service equivalent of Omega Ice's /customer_activity page."""
    from firebase_config import db

    def _sorted(collection_name, limit=100):
        docs = [d.to_dict() for d in db.collection(collection_name).stream()]
        from models.orders import parse_ts
        docs.sort(key=lambda x: parse_ts(x.get("created_at")) or 0, reverse=True)
        return docs[:limit]

    login_logs = _sorted("customer_login_logs")
    activity_logs = _sorted("customer_activity_logs")
    return render_template("customer_activity.html", login_logs=login_logs, activity_logs=activity_logs)
