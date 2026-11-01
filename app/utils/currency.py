from decimal import Decimal, InvalidOperation


CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
}

SUPPORTED_CURRENCIES = frozenset(CURRENCY_SYMBOLS)

INTERNATIONAL_PACKAGE_PRICES = {
    "USD": {
        "Basic Website": 699,
        "Business Website": 1299,
        "Advanced Website": 2499,
        "SEO Starter": 499,
        "SEO Growth": 899,
        "SEO Advanced": 1599,
    },
    "EUR": {
        "Basic Website": 650,
        "Business Website": 1199,
        "Advanced Website": 2299,
        "SEO Starter": 450,
        "SEO Growth": 850,
        "SEO Advanced": 1499,
    },
    "GBP": {
        "Basic Website": 550,
        "Business Website": 999,
        "Advanced Website": 1999,
        "SEO Starter": 390,
        "SEO Growth": 750,
        "SEO Advanced": 1299,
    },
}


def get_package_price(
    package: str,
    currency: str,
    inr_prices: dict[str, int],
) -> Decimal:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported billing currency.")
    if package == "Starter Retainer" and currency != "INR":
        raise ValueError("Starter Retainer billing is currently INR-only.")
    if currency == "INR":
        return Decimal(str(inr_prices.get(package, 48000)))
    price = INTERNATIONAL_PACKAGE_PRICES.get(currency, {}).get(package)
    if price is None:
        raise ValueError("No price is configured for this package and currency.")
    return Decimal(price)


def to_minor_units(amount: int | float | Decimal) -> int:
    value = Decimal(str(amount))
    if not value.is_finite() or value < 0:
        raise ValueError("Payment amount must be a non-negative finite value.")
    minor_units = value * 100
    if minor_units != minor_units.to_integral_value():
        raise ValueError("Payment amount cannot have more than two decimal places.")
    return int(minor_units)


def format_currency(value, currency: str = "INR") -> str:
    if currency not in CURRENCY_SYMBOLS:
        raise ValueError(f"Unsupported billing currency: {currency!r}")
    try:
        amount = Decimal("0") if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Currency amount must be numeric.") from exc
    if not amount.is_finite():
        raise ValueError("Currency amount must be finite.")
    symbol = CURRENCY_SYMBOLS[currency]
    return f"{symbol}{amount:,.2f}"
