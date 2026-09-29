"""MODULE 1: Customer Management + Chat Profile."""

import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from auth import login_required, role_required, super_admin_required
from models import customers as customers_model
from models import chats as chats_model
from models import customer_auth

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")

BARANGAYS_CUYAPO = [
    "Baloy", "Bambanaba", "Bantug", "Bentigan", "Bibiclat", "Bonifacio",
    "Bued", "Bulala", "Burgos", "Cabatuan", "Cabileo", "Cacapasan",
    "Calancuasan Norte", "Calancuasan Sur", "Colosboa", "Columbitin",
    "Curva", "District I (Poblacion I)", "District II (Poblacion II)",
    "District IV (Poblacion IV)", "District V (Poblacion V)",
    "District VI (Poblacion VI)", "District VII (Poblacion VII)",
    "District VIII (Poblacion VIII)", "Landig", "Latap", "Loob", "Luna",
    "Malbeg-Patalan", "Malineng", "Matindeg", "Maycaban", "Nacuralan",
    "Nagmisahan", "Paitan Norte", "Paitan Sur", "Piglisan", "Pugo",
    "Rizal", "Sabit", "Salagusog", "San Antonio", "San Jose", "San Juan",
    "Santa Clara", "Santa Cruz", "Sinimbaan", "Tagtagumbao", "Tutuloy",
    "Ungab", "Villaflores",
]  # 51 official barangays of Cuyapo, Nueva Ecija


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
        customer_types=customers_model.VALID_TYPES,
        type_labels=customers_model.TYPE_LABELS,
    )


@customers_bp.route("/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def create():
    name = request.form.get("name", "").strip()
    customer_type = request.form.get("customer_type", "household").strip()
    business_type = request.form.get("business_type", "").strip()
    business_name = request.form.get("business_name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    barangay = request.form.get("barangay", "").strip()
    password = request.form.get("password", "").strip()

    if not name or not phone or not address:
        flash("Kailangan lahat ng required fields (Complete Name, Phone No., Address).", "danger")
        return redirect(url_for("customers.list_view"))

    # Walk-in customers (customer_type == "household") never have business
    # info - only Resellers do. Ignore anything typed in those fields for a
    # Walk-in, even if the form somehow sent them (e.g. JS was off).
    if customer_type != "reseller":
        business_type = ""
        business_name = ""

    customers_model.create_customer(
        name, customer_type, phone,
        barangay=barangay, address=address,
        business_type=business_type, business_name=business_name,
        password=password or None,
    )
    flash(f"Nadagdag si {name} sa customers. May sarili na syang QR code para sa Customer Portal auto-login.", "success")
    return redirect(url_for("customers.list_view"))


@customers_bp.route("/<customer_id>/edit", methods=["POST"])
@login_required
@role_required("owner", "staff")
def edit(customer_id):
    updates = {}
    # name/phone: never blank these out by accident (a genuinely empty
    # submit for these is treated as "no change", not "clear it").
    for field in ("name", "phone"):
        val = request.form.get(field)
        if val is not None and val.strip():
            updates[field] = val.strip()
    # barangay/address/customer_type: fine to allow clearing to blank.
    for field in ("barangay", "address", "customer_type"):
        val = request.form.get(field)
        if val is not None:
            updates[field] = val.strip()

    # Walk-in customers never keep business info - clear it automatically
    # when switching a Reseller to Walk-in, and ignore whatever the form
    # sent for those fields either way in that case.
    customer_type = updates.get("customer_type") or (customers_model.get_customer(customer_id) or {}).get("customer_type")
    if customer_type != "reseller":
        updates["business_type"] = ""
        updates["business_name"] = ""
    else:
        for field in ("business_type", "business_name"):
            val = request.form.get(field)
            if val is not None:
                updates[field] = val.strip()

    customers_model.update_customer(customer_id, updates)
    flash("Na-update ang customer.", "success")
    return redirect(url_for("customers.list_view"))


@customers_bp.route("/<customer_id>/qr")
@login_required
def qr_image(customer_id):
    """Generates the customer's QR-auto-login code as a PNG, on the fly (not
    stored as a file - always freshly rendered from the customer's current
    qr_token, so regenerate_qr_token() takes effect immediately). Encodes the
    full /customer/qr-login/<token> URL so scanning it with any phone camera
    (not just this app) opens straight into the Customer Portal, already
    logged in - see routes/customer_portal.py's qr_login()."""
    import qrcode

    customer = customers_model.get_customer(customer_id)
    if not customer:
        abort(404)

    login_url = url_for("customer_portal.qr_login", token=customer["qr_token"], _external=True)
    img = qrcode.make(login_url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@customers_bp.route("/<customer_id>/qr/regenerate", methods=["POST"])
@login_required
@role_required("owner", "staff")
def qr_regenerate(customer_id):
    """Invalidates the customer's old printed/saved QR (e.g. nawala o
    napulot ng iba) and issues a fresh one - the old code stops working
    right after this."""
    customer = customers_model.get_customer(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("customers.list_view"))
    customers_model.regenerate_qr_token(customer_id)
    flash(f"Bagong QR code na para kay {customer.get('name')}. Hindi na gagana ang lumang QR nya.", "success")
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
@super_admin_required
def activity_log():
    """MODULE 9: audit trail of Customer Portal logins (success + failed)
    and what logged-in customers did (ordered, redeemed, etc.) - the
    self-service equivalent of Omega Ice's /customer_activity page.

    Locked to ONLY gamboamoises693@gmail.com per owner's explicit request
    (see auth.py's SUPER_ADMIN_EMAIL / super_admin_required) - not even the
    branch owner's own login sees this page anymore."""
    from firebase_config import db

    def _sorted(collection_name, limit=100):
        docs = [d.to_dict() for d in db.collection(collection_name).stream()]
        from models.orders import parse_ts
        docs.sort(key=lambda x: parse_ts(x.get("created_at")) or 0, reverse=True)
        return docs[:limit]

    from models.activity import list_system_activity, get_engagement
    from models.orders import parse_ts
    from datetime import datetime, timezone

    login_logs = _sorted("customer_login_logs")
    activity_logs = _sorted("customer_activity_logs")

    # Owner asked to see Owner + Staff activity specifically (separate from
    # Isesmo's own developer actions) - role_filter drives a tab UI in the
    # template. "all" (default) shows everything, unfiltered, like before.
    role_filter = request.args.get("role", "all")
    valid_roles = {"all", "developer", "owner", "staff", "customer"}
    if role_filter not in valid_roles:
        role_filter = "all"

    # Fetch the full unfiltered set once (cheap - single Firestore read) so
    # we can both compute per-role counts for the tab badges AND slice down
    # to what's actually shown, without hitting Firestore twice.
    all_system_activity = list_system_activity(limit=300)
    role_counts = {"all": len(all_system_activity)}
    for r in ("developer", "owner", "staff", "customer"):
        role_counts[r] = sum(1 for a in all_system_activity if a.get("actor_role") == r)

    if role_filter == "all":
        system_activity = all_system_activity[:150]
    else:
        system_activity = [a for a in all_system_activity if a.get("actor_role") == role_filter][:150]

    for a in system_activity:
        ts = parse_ts(a.get("created_at"))
        a["display_date"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y %I:%M %p") if ts else ""
    engagement = get_engagement()
    return render_template(
        "customer_activity.html",
        login_logs=login_logs,
        activity_logs=activity_logs,
        system_activity=system_activity,
        engagement=engagement,
        role_filter=role_filter,
        role_counts=role_counts,
    )
