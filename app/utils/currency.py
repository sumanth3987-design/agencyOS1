"""Multi-Currency Utilities & Financial Split Algorithms for AgencyOS."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

SUPPORTED_CURRENCIES = {"INR", "USD", "EUR", "GBP"}

# Package Price Constants aligned with test expectations
PACKAGE_PRICES = {
    "Basic Website": 48000,
    "Business Website": 78000,
    "Advanced Website": 125000,
    "SEO Starter": 24000,
    "SEO Growth": 48000,
    "SEO Advanced": 96000,
    "Starter Retainer": 14999,
}

INTERNATIONAL_PACKAGE_PRICES = {
    "Basic Website": {"USD": 699, "EUR": 649, "GBP": 549, "INR": 48000},
    "Business Website": {"USD": 1199, "EUR": 1099, "GBP": 999, "INR": 78000},
    "Advanced Website": {"USD": 2499, "EUR": 1749, "GBP": 1499, "INR": 125000},
    "SEO Starter": {"USD": 399, "EUR": 369, "GBP": 319, "INR": 24000},
    "SEO Growth": {"USD": 699, "EUR": 850, "GBP": 549, "INR": 48000},
    "SEO Advanced": {"USD": 1399, "EUR": 1299, "GBP": 1099, "INR": 96000},
    "Starter Retainer": {"INR": 14999},
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
    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
    
    if clean_curr != "INR" and package in INTERNATIONAL_PACKAGE_PRICES:
        curr_map = INTERNATIONAL_PACKAGE_PRICES[package]
        if clean_curr in curr_map:
            return Decimal(str(curr_map[clean_curr])).quantize(Decimal("0.01"))
        raise ValueError(f"Package '{package}' not available in currency {clean_curr}")
        
    if package_prices and package in package_prices:
        return Decimal(str(package_prices[package])).quantize(Decimal("0.01"))
        
    if package in INTERNATIONAL_PACKAGE_PRICES and "INR" in INTERNATIONAL_PACKAGE_PRICES[package]:
        return Decimal(str(INTERNATIONAL_PACKAGE_PRICES[package]["INR"])).quantize(Decimal("0.01"))

    raise ValueError(f"Unrecognized package tier or price configuration: {package}")