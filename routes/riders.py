"""MODULE 6: Rider Management."""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required, create_user
from models import riders as riders_model

riders_bp = Blueprint("riders", __name__, url_prefix="/riders")


@riders_bp.route("/")
@login_required
def list_view():
    performance = riders_model.rider_performance_today()
    return render_template("riders.html", riders=performance)


@riders_bp.route("/new", methods=["POST"])
@login_required
@role_required("owner")
def create():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    tricycle_no = request.form.get("tricycle_no", "").strip()
    login_email = request.form.get("login_email", "").strip()
    login_password = request.form.get("login_password", "").strip()

    if not name or not phone:
        flash("Kailangan ng name at phone.", "danger")
        return redirect(url_for("riders.list_view"))

    rider = riders_model.create_rider(name, phone, tricycle_no)

    if login_email and login_password:
        create_user(login_email, login_password, name, role="rider", rider_id=rider["id"])
        flash(f"Nadagdag si {name} bilang rider, may login na account.", "success")
    else:
        flash(f"Nadagdag si {name} bilang rider (wala pang login account).", "success")

    return redirect(url_for("riders.list_view"))


@riders_bp.route("/<rider_id>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete(rider_id):
    riders_model.delete_rider(rider_id)
    flash("Na-delete ang rider.", "success")
    return redirect(url_for("riders.list_view"))
