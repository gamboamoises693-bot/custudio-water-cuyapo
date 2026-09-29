"""MODULE 5: Sales & Collection Dashboard + MODULE 7: Reports (Daily Closing, Weekly/Monthly,
Inactive Customers, Best Customers).

Sales figures are computed on-the-fly from the `deliveries` collection (source of truth for
money actually collected) rather than a separate mutable `sales_daily` doc, so numbers can
never drift out of sync with the underlying delivery records. `expenses` is a small standalone
collection for the daily-closing net-income calculation.
"""

from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth import login_required, role_required
from firebase_config import db, server_timestamp
from models import orders as orders_model
from models import customers as customers_model
from models import inventory as inventory_model
from models import loyalty as loyalty_model
from models.activity import record_action

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


def _compute_monthly_financials():
    """Owner-only dashboard widget: current CALENDAR month (1st of this
    month -> today), not the rolling 30-day window Reports uses for its
    "monthly" period - a business owner checking "profit this month"
    expects the actual calendar month-to-date, not a trailing 30 days.
    Sales come from delivered_at (money actually collected, same source
    of truth as the rest of this module); expenses come from the
    `expenses` collection's `date` field."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_deliveries = [
        d for d in orders_model.list_all_deliveries()
        if (ts := orders_model.parse_ts(d.get("delivered_at"))) is not None
        and datetime.fromtimestamp(ts, tz=timezone.utc) >= month_start
    ]
    sales_month = sum(d.get("amount_collected", 0.0) for d in month_deliveries)

    all_expenses = [d.to_dict() for d in db.collection(EXPENSES).stream()]
    month_expenses = [
        e for e in all_expenses
        if (ts := orders_model.parse_ts(e.get("date"))) is not None
        and datetime.fromtimestamp(ts, tz=timezone.utc) >= month_start
    ]
    expenses_month = sum(e.get("amount", 0.0) for e in month_expenses)

    profit_month = sales_month - expenses_month
    profit_margin_month = (profit_month / sales_month * 100.0) if sales_month > 0 else 0.0

    return {
        "sales_month": sales_month,
        "expenses_month": expenses_month,
        "profit_month": profit_month,
        "profit_margin_month": profit_margin_month,
    }


@sales_bp.route("/dashboard")
@login_required
def dashboard():
    today = _today_str()
    today_deliveries = orders_model.list_deliveries_for_date(today)
    totals = _compute_totals(today_deliveries)

    # Owner-only (see templates/dashboard.html gating): Isesmo (the
    # developer/super-admin) is explicitly NOT treated as the business
    # owner for this widget per owner's request, even though that account
    # has super-admin access everywhere else - profit figures are private
    # to the actual business owner. Only computed when it'll actually be
    # shown, to avoid the extra Firestore reads for everyone else.
    from auth import current_user
    _cu = current_user() or {}
    monthly_financials = None
    if _cu.get("role") == "owner" and not _cu.get("is_super_admin"):
        monthly_financials = _compute_monthly_financials()

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

    # MODULE 9 (promo monitoring #1): Loyalty Dashboard Widget - customers
    # who are 80+ gallons into their 100-gallon card, i.e. close to
    # unlocking their next 5 free gallons. Lets the owner proactively
    # remind/upsell them instead of only finding out when it's redeemed.
    near_reward_customers = loyalty_model.list_customers_near_reward(threshold=80)

    return render_template(
        "dashboard.html",
        totals=totals,
        walkin_vs_delivered=walkin_vs_delivered,
        tanks=tanks,
        low_tank_alerts=low_tank_alerts,
        low_stock_items=low_stock_items,
        pending_orders=pending_orders,
        on_delivery_orders=on_delivery_orders,
        near_reward_customers=near_reward_customers,
        loyalty_reward_threshold=loyalty_model.GALLONS_PER_REWARD,
        today=today,
        monthly_financials=monthly_financials,
    )


@sales_bp.route("/reports")
@login_required
def reports():
    period = request.args.get("period", "daily")  # daily | weekly | monthly
    today = datetime.now(timezone.utc).date()

    # Custom date-range filter ("filter ng date para ma-search yung araw na
    # gusto balikan") - takes over from the Daily/Weekly/Monthly quick
    # buttons whenever a start_date is given. end_date defaults to today
    # (a single start_date alone means "from that day up to now").
    filter_start = request.args.get("start_date") or ""
    filter_end = request.args.get("end_date") or ""
    range_end = today
    if filter_start:
        try:
            start_date = datetime.strptime(filter_start, "%Y-%m-%d").date()
        except ValueError:
            start_date = today
        if filter_end:
            try:
                range_end = datetime.strptime(filter_end, "%Y-%m-%d").date()
            except ValueError:
                range_end = today
        period = "custom"
    elif period == "weekly":
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
        return start_date <= d_date <= range_end

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

    # MODULE 9 (promo monitoring #2): Promo Redemption Report - how much of
    # the loyalty program was actually redeemed this period. Uses order_date
    # (not delivered_at) since redemption happens the moment an order is
    # PLACED (see routes/customer_portal.py api_place_order() ->
    # loyalty.apply_free_gallons()), same period-filter pattern as deliveries.
    def order_in_range(o):
        ts = orders_model.parse_ts(o.get("order_date"))
        if ts is None:
            return False
        d_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return start_date <= d_date <= range_end

    period_orders = [o for o in orders_model.list_orders() if order_in_range(o)]
    redemption_summary = loyalty_model.get_redemption_summary(period_orders)

    all_expenses = [d.to_dict() for d in db.collection(EXPENSES).stream()]

    today_expenses = [
        e for e in all_expenses
        if orders_model.parse_ts(e.get("date")) and
        datetime.fromtimestamp(orders_model.parse_ts(e.get("date")), tz=timezone.utc).date() == today
    ]
    total_expenses_today = sum(e.get("amount", 0.0) for e in today_expenses)
    today_deliveries = orders_model.list_deliveries_for_date(_today_str())
    today_totals = _compute_totals(today_deliveries)
    net_income_today = today_totals["total_sales"] - total_expenses_today

    # Expense Breakdown (editable/deletable - owner/staff, e.g. Cindy) for
    # whichever period/date-range is currently selected above, newest first.
    def expense_in_range(e):
        ts = orders_model.parse_ts(e.get("date"))
        if ts is None:
            return False
        d_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return start_date <= d_date <= range_end

    period_expenses = [e for e in all_expenses if expense_in_range(e)]
    period_expenses.sort(key=lambda e: orders_model.parse_ts(e.get("date")) or 0, reverse=True)
    total_period_expenses = sum(e.get("amount", 0.0) for e in period_expenses)
    for e in period_expenses:
        ts = orders_model.parse_ts(e.get("date"))
        e["display_date"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y") if ts else ""

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
        redemption_summary=redemption_summary,
        period_expenses=period_expenses,
        total_period_expenses=total_period_expenses,
        filter_start=filter_start,
        filter_end=filter_end,
    )


GRANULARITY_BUCKET_LIMIT = {"daily": 30, "weekly": 26, "monthly": 12, "quarterly": 8, "yearly": 6}
GRANULARITY_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly", "yearly": "Yearly"}


def _bucket_key(dt, granularity):
    if granularity == "daily":
        return dt.strftime("%Y-%m-%d")
    if granularity == "weekly":
        iso = dt.isocalendar()  # (iso_year, iso_week, iso_weekday)
        return f"{iso[0]}-W{iso[1]:02d}"
    if granularity == "quarterly":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    if granularity == "yearly":
        return str(dt.year)
    return dt.strftime("%Y-%m")  # monthly (default)


def _compute_sales_trend(granularity):
    """MODULE 5/7 (Sales Trend graph): buckets every delivery's
    amount_collected by day/week/month/quarter/year, sorted chronologically,
    capped to the most recent N buckets per granularity so the chart stays
    readable. Bucket keys are zero-padded ISO-ish strings (YYYY-MM-DD,
    YYYY-Www, YYYY-MM, YYYY-Qn, YYYY) so a plain string sort is already
    chronological order."""
    if granularity not in GRANULARITY_BUCKET_LIMIT:
        granularity = "monthly"

    buckets = {}
    for d in orders_model.list_all_deliveries():
        ts = orders_model.parse_ts(d.get("delivered_at"))
        if ts is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        key = _bucket_key(dt, granularity)
        buckets[key] = buckets.get(key, 0.0) + d.get("amount_collected", 0.0)

    keys_sorted = sorted(buckets.keys())
    limit = GRANULARITY_BUCKET_LIMIT[granularity]
    keys_sorted = keys_sorted[-limit:]
    return {
        "granularity": granularity,
        "labels": keys_sorted,
        "values": [round(buckets[k], 2) for k in keys_sorted],
    }


@sales_bp.route("/reports/trend-data")
@login_required
def trend_data():
    """JSON API for the Sales Trend chart (see templates/reports.html) -
    called via fetch() when the person switches Daily/Weekly/Monthly/
    Quarterly/Yearly tabs, so the page never needs a full reload."""
    granularity = request.args.get("granularity", "monthly")
    return jsonify(_compute_sales_trend(granularity))


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
    record_action("Expense Added", f"{label} - ₱{amount_val:.2f}")
    flash("Naidagdag ang expense.", "success")
    return redirect(url_for("sales.reports"))


@sales_bp.route("/reports/expenses/<expense_id>/edit", methods=["POST"])
@login_required
@role_required("owner", "staff")
def edit_expense(expense_id):
    """Lets Cindy (or Isesmo) fix a typo'd label/amount on a past expense -
    part of the Expense Breakdown (see reports() above). Keeps the
    expense's original date (only label/amount change)."""
    label = request.form.get("label", "").strip()
    amount = request.form.get("amount", "0")
    try:
        amount_val = float(amount)
    except ValueError:
        amount_val = 0.0

    ref = db.collection(EXPENSES).document(expense_id)
    existing = ref.get()
    if not existing.exists:
        flash("Wala nang ganitong expense.", "danger")
        return redirect(url_for("sales.reports"))

    if not label or amount_val <= 0:
        flash("Kailangan ng label at valid na amount.", "danger")
        return redirect(url_for("sales.reports"))

    ref.update({"label": label, "amount": amount_val})
    record_action("Expense Edited", f"{label} - ₱{amount_val:.2f}")
    flash("Na-update ang expense.", "success")
    return redirect(url_for("sales.reports"))


@sales_bp.route("/reports/expenses/<expense_id>/delete", methods=["POST"])
@login_required
@role_required("owner", "staff")
def delete_expense(expense_id):
    ref = db.collection(EXPENSES).document(expense_id)
    existing = ref.get()
    if not existing.exists:
        flash("Wala nang ganitong expense.", "danger")
        return redirect(url_for("sales.reports"))

    data = existing.to_dict()
    ref.delete()
    record_action("Expense Deleted", f"{data.get('label')} - ₱{data.get('amount', 0):.2f}")
    flash("Na-delete ang expense.", "success")
    return redirect(url_for("sales.reports"))
