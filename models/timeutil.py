"""
Centralized Asia/Manila (UTC+8, no daylight saving) time helpers.

ROOT CAUSE this file fixes: every timestamp this app stores is in UTC
(firebase_config.py's server_timestamp() -> firestore.SERVER_TIMESTAMP for
real Firebase, or datetime.now(timezone.utc) for the local mock DB) - that
part is correct and should NOT change (UTC is the right thing to store).
The bug was that every place that turned a stored timestamp into something
a human reads (a chat bubble's time, an order's date, an expense's date) -
or that decided what "today"/"this month" means for Daily Closing and
Reports - used `timezone.utc` directly instead of converting to the
branch's actual local time (Asia/Manila, UTC+8). That made every displayed
date/time run 8 hours behind Manila, and made "today" flip over at 8:00 AM
Manila time instead of midnight.

This module is now the ONE place that UTC -> Manila conversion happens, so
every screen (chat, orders, deliveries, expenses, reports) agrees.
"""

from datetime import datetime, timezone, timedelta

# Philippines has used a single fixed UTC+8 offset with no daylight saving
# since 1978, so a plain fixed-offset timezone is correct here and avoids
# depending on the system having the IANA "Asia/Manila" tzdata installed
# (not guaranteed on every host).
MANILA_TZ = timezone(timedelta(hours=8), name="Asia/Manila")


def parse_ts(value):
    """Best-effort parse of ANY timestamp shape this app stores - an ISO
    string (local mock DB), a real datetime, Firestore's
    DatetimeWithNanoseconds (a datetime subclass), or None/an unresolved
    firestore.SERVER_TIMESTAMP sentinel - into a tz-AWARE UTC datetime.
    Returns None if it can't be parsed yet; callers should treat that as
    "no timestamp available" rather than crash."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def to_manila(value):
    """Converts any storable timestamp shape into a tz-aware Asia/Manila
    datetime, or None if it can't be parsed (e.g. a SERVER_TIMESTAMP
    sentinel that hasn't resolved on this exact read - vanishingly rare,
    just means "no display value yet")."""
    dt = parse_ts(value)
    return dt.astimezone(MANILA_TZ) if dt else None


def now_manila():
    """Current moment, in Asia/Manila local time."""
    return datetime.now(timezone.utc).astimezone(MANILA_TZ)


def today_manila():
    """Today's DATE in Asia/Manila - use this (not
    datetime.now(timezone.utc).date()) anywhere "today"/"this month"
    decides what counts for Daily Closing, Reports, or inactive-customer
    checks, so the day rolls over at Manila midnight, not 8AM Manila."""
    return now_manila().date()


def format_dt(value, fmt="%b %d, %Y %I:%M %p", default=""):
    """Full date + time, Manila local, e.g. 'Sep 29, 2026 03:45 PM'."""
    dt = to_manila(value)
    return dt.strftime(fmt) if dt else default


def format_date(value, fmt="%b %d, %Y", default=""):
    return format_dt(value, fmt, default)


def format_time(value, fmt="%I:%M %p", default=""):
    """Time only, Manila local, e.g. '03:45 PM' - used for chat bubbles."""
    return format_dt(value, fmt, default)
