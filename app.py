"""
CUSTODIO WATER REFILLING STATION - Cuyapo Branch
Main Flask application entrypoint.

Run locally:
    pip install -r requirements.txt
    python app.py

Deploy on Render:
    gunicorn app:app
"""

import os
from flask import Flask, Blueprint, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()

# firebase_config must be imported before routes/models that use `db`
import firebase_config  # noqa: F401
from auth import verify_login, login_required

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB max upload (GCash receipts/photos)

    # ---- auth blueprint (login/logout) ----
    auth_bp = Blueprint("auth", __name__)

    @auth_bp.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            user = verify_login(email, password)
            if user:
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_role"] = user["role"]
                session["user_email"] = user["email"]
                session["rider_id"] = user.get("rider_id")
                flash(f"Welcome back, {user['name']}!", "success")
                next_url = request.args.get("next") or url_for("sales.dashboard")
                return redirect(next_url)
            flash("Mali ang email o password.", "danger")
        return render_template("login.html")

    @auth_bp.route("/logout")
    def logout():
        session.clear()
        flash("Na-logout ka na.", "success")
        return redirect(url_for("auth.login"))

    app.register_blueprint(auth_bp)

    # ---- feature blueprints ----
    from routes.customers import customers_bp
    from routes.chats import chats_bp
    from routes.orders import orders_bp
    from routes.deliveries import deliveries_bp
    from routes.inventory import inventory_bp
    from routes.sales import sales_bp
    from routes.customer_portal import customer_portal_bp
    from routes.accounts import accounts_bp

    app.register_blueprint(customers_bp)
    app.register_blueprint(chats_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(deliveries_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(customer_portal_bp)
    app.register_blueprint(accounts_bp)

    @app.route("/api/staff/push/subscribe", methods=["POST"])
    @login_required
    def staff_push_subscribe():
        """Lets a logged-in owner/staff device register for push
        notifications. Stored with the account's EMAIL (not just "role")
        so push_notify.send_push_to_isesmo() can target Isesmo's device
        specifically - per owner's request that Isesmo get notified of
        every action in the app, whether or not any other staff device is
        also subscribed. See templates/base.html's "🔔 Enable
        Notifications" button (only shown to the super-admin account)."""
        from flask import jsonify
        import push_notify
        from firebase_config import db as _db

        if not push_notify.PUSH_ENABLED:
            return jsonify({"ok": False, "error": "Push not configured on this server."}), 400

        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint")
        keys = data.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            return jsonify({"ok": False, "error": "Invalid subscription"}), 400

        sub_id = push_notify.subscription_id(endpoint)
        _db.collection("staff_push_subscriptions").document(sub_id).set({
            "endpoint": endpoint,
            "keys": {"p256dh": keys.get("p256dh"), "auth": keys.get("auth")},
            "user_id": session.get("user_id"),
            "email": (session.get("user_email") or "").strip().lower(),
            "role": session.get("user_role"),
        })
        return jsonify({"ok": True})

    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("sales.dashboard"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    # Make current user + unread chat count available in every template
    @app.context_processor
    def inject_globals():
        from auth import current_user
        unread = 0
        if "user_id" in session:
            try:
                from models.chats import total_unread_for_owner
                unread = total_unread_for_owner()
            except Exception:
                unread = 0

        # Logo robustness: auto-detect whichever image extension actually
        # made it onto disk (static/images/logo.*) instead of hardcoding
        # logo.jpg everywhere. The most common cause of "hindi lumalabas
        # ang logo" on a mobile GitHub upload is uploading it as a
        # different extension (or to the wrong folder) than the code
        # expects - this way, re-uploading as .png/.jpeg/.webp just works
        # without needing another code change. Falls back to logo.jpg (with
        # the onerror="hide" already on every <img> tag) if nothing is found.
        logo_filename = "images/logo.jpg"
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidate = os.path.join(app.static_folder, "images", f"logo.{ext}")
            if os.path.isfile(candidate):
                logo_filename = f"images/logo.{ext}"
                break

        import push_notify
        return {
            "current_user": current_user(),
            "unread_chats": unread,
            "logo_filename": logo_filename,
            "push_enabled": push_notify.PUSH_ENABLED,
            "vapid_public_key": push_notify.VAPID_PUBLIC_KEY,
        }

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
