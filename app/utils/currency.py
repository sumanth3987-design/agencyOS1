"""Multi-currency pricing, normalization, and statutory calculations for AgencyOS."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, Tuple


CURRENCY_SYMBOLS: Dict[str, str] = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
}

SUPPORTED_CURRENCIES = frozenset(CURRENCY_SYMBOLS)
DEFAULT_CURRENCY = "INR"

INTERNATIONAL_PACKAGE_PRICES: Dict[str, Dict[str, int]] = {
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


def sanitize_currency(currency: str | None) -> str:
    """Validate and sanitize currency code with fallback to default."""
    if not currency:
        return DEFAULT_CURRENCY
    normalized = currency.strip().upper()
    return normalized if normalized in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY


def get_package_price(
    package: str,
    currency: str,
    inr_prices: dict[str, int],
) -> Decimal:
    """Return canonical package price as Decimal for the requested currency."""
    curr = sanitize_currency(currency)
    if package == "Starter Retainer" and curr != "INR":
        raise ValueError("Starter Retainer billing is currently INR-only.")
    if curr == "INR":
        return Decimal(str(inr_prices.get(package, 48000)))
    price = INTERNATIONAL_PACKAGE_PRICES.get(curr, {}).get(package)
    if price is None:
        raise ValueError(f"No price configured for package '{package}' in currency '{curr}'.")
    return Decimal(str(price))


def to_minor_units(amount: int | float | Decimal) -> int:
    """Convert human-readable currency amount to integer minor units (paise/cents/pence)."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Payment amount must be numeric.") from exc

    if not value.is_finite() or value < 0:
        raise ValueError("Payment amount must be a non-negative finite value.")

    minor_units = (value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor_units)


def from_minor_units(minor_amount: int) -> Decimal:
    """Convert integer minor units back to standard Decimal."""
    return (Decimal(minor_amount) / Decimal("100")).quantize(Decimal("0.01"))


def format_currency(value, currency: str = "INR") -> str:
    """Format numeric value with standard currency symbols and tabular commas."""
    curr = sanitize_currency(currency)
    try:
        amount = Decimal("0") if value is None else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Currency amount must be numeric.") from exc

    if not amount.is_finite():
        raise ValueError("Currency amount must be finite.")

    symbol = CURRENCY_SYMBOLS.get(curr, "₹")
    return f"{symbol}{amount:,.2f}"


def calculate_tax_rate(client_country: str, currency: str) -> Decimal:
    """Return statutory tax rate applicable for the client billing context.

    Domestic Indian transactions (INR) require standard 18% GST.
    Cross-border transactions (USD, EUR, GBP) operate under export LUT (0% GST).
    """
    curr = sanitize_currency(currency)
    if curr == "INR":
        return Decimal("0.18")
    return Decimal("0.00")


def compute_financial_split(gross_amount: Decimal, currency: str = "INR") -> Tuple[Decimal, Decimal, Decimal]:
    """Compute talent share (45%), agency profit (55%), and tax portion with 2-decimal precision.

    Returns:
        (talent_share, agency_profit, tax_amount)
    """
    curr = sanitize_currency(currency)
    gross = Decimal(str(gross_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax_rate = calculate_tax_rate("", curr)

    if tax_rate > Decimal("0.00"):
        tax_amount = (gross - (gross / (Decimal("1.00") + tax_rate))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        tax_amount = Decimal("0.00")

    talent_share = (gross * Decimal("0.45")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    agency_share = gross - talent_share

    return talent_share, agency_share, tax_amount