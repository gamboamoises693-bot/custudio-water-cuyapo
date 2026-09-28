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

    app.register_blueprint(customers_bp)
    app.register_blueprint(chats_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(deliveries_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(customer_portal_bp)

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
        return {"current_user": current_user(), "unread_chats": unread}

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
