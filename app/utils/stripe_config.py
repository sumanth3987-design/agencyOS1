import os
import stripe
from decimal import Decimal

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

STRIPE_PRICE_MATRIX = {
    "USD": {
        "Basic Website": "price_usd_basic_placeholder",
        "Business Website": "price_usd_business_placeholder",
        "Advanced Website": "price_usd_advanced_placeholder",
        "SEO Starter": "price_usd_seo_starter_placeholder",
        "SEO Growth": "price_usd_seo_growth_placeholder",
        "SEO Advanced": "price_usd_seo_advanced_placeholder",
    },
    "EUR": {
        "Basic Website": "price_eur_basic_placeholder",
        "Business Website": "price_eur_business_placeholder",
        "Advanced Website": "price_eur_advanced_placeholder",
        "SEO Starter": "price_eur_seo_starter_placeholder",
        "SEO Growth": "price_eur_seo_growth_placeholder",
        "SEO Advanced": "price_eur_seo_advanced_placeholder",
    },
    "GBP": {
        "Basic Website": "price_gbp_basic_placeholder",
        "Business Website": "price_gbp_business_placeholder",
        "Advanced Website": "price_gbp_advanced_placeholder",
        "SEO Starter": "price_gbp_seo_starter_placeholder",
        "SEO Growth": "price_gbp_seo_growth_placeholder",
        "SEO Advanced": "price_gbp_seo_advanced_placeholder",
    },
}

SUPPORTED_STRIPE_CURRENCIES = frozenset(STRIPE_PRICE_MATRIX.keys())


def get_stripe_price_id(currency: str, package_name: str) -> str:
    """Look up the Stripe Price ID for a given currency and package."""
    if currency not in STRIPE_PRICE_MATRIX:
        raise ValueError(f"Unsupported currency for Stripe: {currency}")
    if package_name not in STRIPE_PRICE_MATRIX[currency]:
        raise ValueError(
            f"Package '{package_name}' not found in Stripe price matrix for {currency}"
        )
    return STRIPE_PRICE_MATRIX[currency][package_name]


def build_checkout_session_params(
    project_id: int,
    package_name: str,
    currency: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Build Stripe Checkout Session creation parameters."""
    price_id = get_stripe_price_id(currency, package_name)
    return {
        "payment_method_types": ["card"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(project_id),
        "metadata": {
            "project_id": str(project_id),
            "package_name": package_name,
            "currency": currency,
        },
    }