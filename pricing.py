"""
Container/product catalog - the OFFICIAL price list (walk-in/store/others),
replacing the old per-customer negotiated `price_per_container` field.
Every order (staff-created, chat-created, or self-service via the Customer
Portal) now prices itself from THIS catalog instead of a price stored on
the customer doc.

Assumption worth confirming with the owner (cheap to change - it's all
right here in one place): "gallons" below is how much each container
counts toward the loyalty card's "every 100 gallons = free 5 gallons"
promo. Taken literally from the price list's own labels for the two
1-gallon types and the 6-gallon type; the 6-10 Liters small bottle isn't
labeled in gallons, so it's approximated at ~2 gallons (8L, the midpoint
of 6-10L, converted at 3.785 L/gallon). If that approximation is wrong,
only GALLONS_EQUIVALENT below for "small_bottle" needs to change.
"""

CONTAINER_TYPES = {
    "gallon_slim": {
        "label": "1 Gallon (Slim)",
        "price": 30.0,
        "gallons": 1,
    },
    "gallon_round": {
        "label": "1 Gallon (Round)",
        "price": 30.0,
        "gallons": 1,
    },
    "six_gallon": {
        "label": "6 Gallon",
        "price": 150.0,
        "gallons": 6,
    },
    "small_bottle": {
        "label": "6-10 Liters (Small Bottle)",
        "price": 15.0,
        "gallons": 2,  # approximation - see module docstring
    },
}

# Display order (matches the order on the price list photo)
CONTAINER_TYPE_ORDER = ["gallon_slim", "gallon_round", "six_gallon", "small_bottle"]

DEFAULT_CONTAINER_TYPE = "gallon_slim"


def list_container_types():
    """Returns the catalog as an ordered list of dicts (for rendering a
    selector in templates/JSON APIs)."""
    return [{"key": key, **CONTAINER_TYPES[key]} for key in CONTAINER_TYPE_ORDER]


def get_container_type(key):
    """Returns the catalog entry dict for `key`, or None if it's not a
    recognized container type."""
    return CONTAINER_TYPES.get(key)


def is_valid_container_type(key):
    return key in CONTAINER_TYPES
