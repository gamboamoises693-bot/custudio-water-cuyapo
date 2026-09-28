"""MODULE: Accounts (owner/staff LOGIN management - not customers).

Locked to ONLY gamboamoises693@gmail.com (see auth.py's SUPER_ADMIN_EMAIL) -
per owner's explicit request, not even the branch owner's own
custodiocindy220@gmail.com login can see this page anymore. Lets that one
super-admin account see every owner/staff login on the system and reset
ANY of their passwords, without touching Firestore directly or running a
one-off script (update_login_accounts.py) every time.

Kept deliberately simple: view all accounts + reset password + add a new
staff account. Does NOT allow deleting the last remaining owner account
(would lock everyone out) or changing your own role by accident.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from auth import login_required, super_admin_required, list_users, get_user, create_user, update_user_password, get_user_by_email

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@accounts_bp.route("/")
@login_required
@super_admin_required
def list_view():
    return render_template("accounts.html", users=list_users())


@accounts_bp.route("/new", methods=["POST"])
@login_required
@super_admin_required
def create():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "staff").strip()

    if not name or not email or not password:
        flash("Kailangan lahat ng fields (Pangalan, Email, Password).", "danger")
        return redirect(url_for("accounts.list_view"))
    if role not in ("owner", "staff"):
        role = "staff"
    if len(password) < 4:
        flash("Kailangan ng minimum 4 characters ang password.", "danger")
        return redirect(url_for("accounts.list_view"))
    if get_user_by_email(email):
        flash(f"May account na gamit ang email na {email}.", "danger")
        return redirect(url_for("accounts.list_view"))

    create_user(email, password, name, role=role)
    flash(f"Nagawa ang bagong {role} account para kay {name} ({email}).", "success")
    return redirect(url_for("accounts.list_view"))


@accounts_bp.route("/<user_id>/reset-password", methods=["POST"])
@login_required
@super_admin_required
def reset_password(user_id):
    new_password = request.form.get("new_password", "").strip()
    if len(new_password) < 4:
        flash("Kailangan ng minimum 4 characters ang password.", "danger")
        return redirect(url_for("accounts.list_view"))

    user = get_user(user_id)
    if not user:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_view"))

    update_user_password(user_id, new_password)
    flash(f"Na-reset na ang password ni {user.get('name')} ({user.get('email')}). Ipaalam mo sa kanya ang bagong password.", "success")
    return redirect(url_for("accounts.list_view"))
