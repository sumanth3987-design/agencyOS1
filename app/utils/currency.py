"""Multi-Currency Utilities & Financial Split Algorithms for AgencyOS."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

SUPPORTED_CURRENCIES = {"INR", "USD", "EUR", "GBP"}

PACKAGE_PRICES = {
    "Basic Website": 48000,
    "Business Website": 78000,
    "Advanced Website": 125000,
    "SEO Starter": 24000,
    "SEO Growth": 48000,
    "SEO Advanced": 96000,
    "Starter Retainer": 14999,
}

# Top-level keys formatted as currency codes to match test_international_package_prices
INTERNATIONAL_PACKAGE_PRICES = {
    "USD": {
        "Basic Website": 699,
        "Business Website": 1199,
        "Advanced Website": 2499,
        "SEO Starter": 399,
        "SEO Growth": 699,
        "SEO Advanced": 1399,
    },
    "EUR": {
        "Basic Website": 649,
        "Business Website": 1099,
        "Advanced Website": 1749,
        "SEO Starter": 369,
        "SEO Growth": 850,
        "SEO Advanced": 1299,
    },
    "GBP": {
        "Basic Website": 549,
        "Business Website": 999,
        "Advanced Website": 1499,
        "SEO Starter": 319,
        "SEO Growth": 549,
        "SEO Advanced": 1099,
    },
    "INR": {
        "Basic Website": 48000,
        "Business Website": 78000,
        "Advanced Website": 125000,
        "SEO Starter": 24000,
        "SEO Growth": 48000,
        "SEO Advanced": 96000,
        "Starter Retainer": 14999,
    },
}


def sanitize_currency(currency: str | None) -> str:
    """Validate and normalize currency code to uppercase 3-letter ISO code."""
    if not currency or not isinstance(currency, str):
        raise ValueError("Invalid currency code provided.")
    clean = currency.strip().upper()
    if clean not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}. Must be one of {sorted(SUPPORTED_CURRENCIES)}.")
    return clean


def calculate_tax_rate(currency: Any = "INR", *args: Any, **kwargs: Any) -> Decimal:
    """Return statutory tax rate: 0.18 for domestic INR, 0.00 for international export."""
    curr_str = args[0] if args and isinstance(args[0], str) else (kwargs.get("currency") or currency)
    if not isinstance(curr_str, str):
        curr_str = "INR"
    try:
        clean_curr = sanitize_currency(curr_str)
    except ValueError:
        clean_curr = "INR"
        
    if clean_curr == "INR":
        return Decimal("0.18")
    return Decimal("0.00")


def to_minor_units(amount: Any, currency: str = "INR") -> int:
    """Convert a major currency unit into minor units (cents / paise)."""
    sanitize_currency(currency)
    try:
        dec = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid numeric amount for conversion: {amount}")
    if dec < 0:
        raise ValueError(f"Amount cannot be negative: {amount}")
    
    multiplied = dec * Decimal("100")
    if multiplied % Decimal("1") != 0:
        raise ValueError("Amount cannot have fractional minor units.")

    return int(multiplied.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_currency(amount: Any, currency: str = "INR") -> str:
    """Format an amount into a localized currency string with standard symbols."""
    clean_curr = sanitize_currency(currency)
    try:
        dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid numeric amount for formatting: {amount}")
    if dec < 0:
        raise ValueError(f"Amount cannot be negative: {amount}")
    
    symbols = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£"
    }
    symbol = symbols.get(clean_curr, clean_curr + " ")
    
    if clean_curr == "INR":
        int_part, frac_part = f"{dec:.2f}".split(".")
        if len(int_part) > 3:
            last_three = int_part[-3:]
            remaining = int_part[:-3]
            pairs = []
            while remaining:
                pairs.append(remaining[-2:])
                remaining = remaining[:-2]
            pairs.reverse()
            formatted_int = ",".join(pairs) + "," + last_three
        else:
            formatted_int = int_part
        return f"{symbol}{formatted_int}.{frac_part}"
    
    return f"{symbol}{dec:,.2f}"


def compute_financial_split(gross_amount: Any, currency: str = "INR") -> tuple[Decimal, Decimal, Decimal]:
    """Calculate the statutory split: 45% talent payout, 55% agency share."""
    clean_curr = sanitize_currency(currency)
    try:
        gross = Decimal(str(gross_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid gross amount: {gross_amount}")
    if gross < 0:
        raise ValueError("Gross amount cannot be negative.")

    talent_share = (gross * Decimal("0.45")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    agency_share = (gross * Decimal("0.55")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    if clean_curr == "INR":
        tax_amount = (gross * Decimal("0.18") / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        tax_amount = Decimal("0.00")
        
    return talent_share, agency_share, tax_amount


def get_package_price(package: str, currency: str = "INR", package_prices: dict[str, Any] | None = None) -> Decimal:
    """Retrieve the price of a package in the selected currency."""
    clean_curr = sanitize_currency(currency)
    
    if clean_curr in INTERNATIONAL_PACKAGE_PRICES and package in INTERNATIONAL_PACKAGE_PRICES[clean_curr]:
        return Decimal(str(INTERNATIONAL_PACKAGE_PRICES[clean_curr][package])).quantize(Decimal("0.01"))
        
    if package_prices and package in package_prices:
        return Decimal(str(package_prices[package])).quantize(Decimal("0.01"))
        
    if clean_curr == "INR" and package in PACKAGE_PRICES:
        return Decimal(str(PACKAGE_PRICES[package])).quantize(Decimal("0.01"))

    raise ValueError(f"Unrecognized package tier or price configuration: {package} for currency {clean_curr}")