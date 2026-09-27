"""MODULE 5: Sales & Collection Dashboard + MODULE 7: Reports (Daily Closing, Weekly/Monthly,
Inactive Customers, Best Customers).

Sales figures are computed on-the-fly from the `deliveries` collection (source of truth for
money actually collected) rather than a separate mutable `sales_daily` doc, so numbers can
never drift out of sync with the underlying delivery records. `expenses` is a small standalone
collection for the daily-closing net-income calculation.
"""

from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, role_required
from firebase_config import db, server_timestamp
from models import orders as orders_model
from models import customers as customers_model
from models import inventory as inventory_model

sales_bp = Blueprint("sales", __name__)

EXPENSES = "expenses"


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _compute_totals(deliveries):
    total_sales = sum(d.get("amount_collected", 0.0) for d in deliveries)
    total_containers = sum(d.get("delivered_qty", 0) for d in deliveries)
    gcash = sum(d.get("amount_collected", 0.0) for d in deliveries if d.get("payment_type") == "gcash")
    cash = sum(d.get("amount_collected", 0.0) for d in deliveries if d.get("payment_type") == "cash")
    utang = sum(
        # for utang deliveries, "collected" is usually 0; report the amount owed instead
        (orders_model.get_order(d.get("order_id")) or {}).get("amount_due", 0.0)
        for d in deliveries if d.get("payment_type") == "utang"
    )
    return {
        "total_sales": total_sales,
        "total_containers": total_containers,
        "gcash": gcash,
        "cash": cash,
        "utang": utang,
    }


@sales_bp.route("/dashboard")
@login_required
def dashboard():
    today = _today_str()
    today_deliveries = orders_model.list_deliveries_for_date(today)
    totals = _compute_totals(today_deliveries)

    walkin_vs_delivered = {
        "delivered": len(today_deliveries),
        # "walk-in" sales aren't tracked as a separate order type in this MVP yet;
        # surfaced as 0 here with a clear TODO rather than a fabricated number.
        "walkin": 0,
    }

    tanks = inventory_model.list_tanks()
    low_tank_alerts = inventory_model.get_low_tank_alerts()
    low_stock_items = inventory_model.get_low_stock_items()
    pending_orders = orders_model.list_orders(status="pending")
    on_delivery_orders = orders_model.list_orders(status="on_delivery")

    return render_template(
        "dashboard.html",
        totals=totals,
        walkin_vs_delivered=walkin_vs_delivered,
        tanks=tanks,
        low_tank_alerts=low_tank_alerts,
        low_stock_items=low_stock_items,
        pending_orders=pending_orders,
        on_delivery_orders=on_delivery_orders,
        today=today,
    )


@sales_bp.route("/reports")
@login_required
def reports():
    period = request.args.get("period", "daily")  # daily | weekly | monthly
    today = datetime.now(timezone.utc).date()

    if period == "weekly":
        start_date = today - timedelta(days=7)
    elif period == "monthly":
        start_date = today - timedelta(days=30)
    else:
        start_date = today

    all_deliveries = orders_model.list_all_deliveries()

    def in_range(d):
        ts = orders_model.parse_ts(d.get("delivered_at"))
        if ts is None:
            return False
        d_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return start_date <= d_date <= today

    period_deliveries = [d for d in all_deliveries if in_range(d)]
    totals = _compute_totals(period_deliveries)

    # Per-barangay breakdown ("alin barangay malakas sa Cuyapo")
    barangay_totals = {}
    customers_by_id = {c["id"]: c for c in customers_model.list_customers()}
    for d in period_deliveries:
        cust = customers_by_id.get(d.get("customer_id"))
        barangay = cust.get("barangay", "Unknown") if cust else "Unknown"
        barangay_totals[barangay] = barangay_totals.get(barangay, 0.0) + d.get("amount_collected", 0.0)
    barangay_ranking = sorted(barangay_totals.items(), key=lambda kv: kv[1], reverse=True)

    inactive_customers = customers_model.list_inactive_customers(days=14)
    best_customers = customers_model.top_customers(limit=10)

    expenses = [d.to_dict() for d in db.collection(EXPENSES).stream()]
    today_expenses = [
        e for e in expenses
        if orders_model.parse_ts(e.get("date")) and
        datetime.fromtimestamp(orders_model.parse_ts(e.get("date")), tz=timezone.utc).date() == today
    ]
    total_expenses_today = sum(e.get("amount", 0.0) for e in today_expenses)
    today_deliveries = orders_model.list_deliveries_for_date(_today_str())
    today_totals = _compute_totals(today_deliveries)
    net_income_today = today_totals["total_sales"] - total_expenses_today

    return render_template(
        "reports.html",
        period=period,
        totals=totals,
        barangay_ranking=barangay_ranking,
        inactive_customers=inactive_customers,
        best_customers=best_customers,
        net_income_today=net_income_today,
        total_expenses_today=total_expenses_today,
        today_totals=today_totals,
    )


@sales_bp.route("/reports/expenses/new", methods=["POST"])
@login_required
@role_required("owner", "staff")
def add_expense():
    label = request.form.get("label", "").strip()
    amount = request.form.get("amount", "0")
    try:
        amount_val = float(amount)
    except ValueError:
        amount_val = 0.0

    if not label or amount_val <= 0:
        flash("Kailangan ng label at valid na amount.", "danger")
        return redirect(url_for("sales.reports"))

    db.collection(EXPENSES).document().set({
        "label": label,
        "amount": amount_val,
        "date": server_timestamp(),
    })
    flash("Naidagdag ang expense.", "success")
    return redirect(url_for("sales.reports"))
