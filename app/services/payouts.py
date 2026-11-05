"""AgencyOS RazorpayX Payouts Service.

Executes direct fund disbursements to specialist UPI IDs or Bank accounts via RazorpayX API,
with automatic sandbox simulation fallback for local development.
"""

import base64
import json
import logging
import os
import secrets
import urllib.request
from decimal import Decimal

logger = logging.getLogger(__name__)


def disburse_payout(withdrawal_req) -> tuple[bool, str, str]:
    """Disburse funds via RazorpayX Payouts API or fallback to sandbox simulation.
    
    Returns: (success: bool, utr_or_reference: str, message: str)
    """
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    rx_account_number = os.getenv("RAZORPAYX_ACCOUNT_NUMBER", "78910111213")

    is_sandbox = not key_id or "placeholder" in key_id.lower() or "test" in key_id.lower()

    specialist = withdrawal_req.specialist
    amount_paise = int(round(float(withdrawal_req.amount) * 100))
    ref_id = f"wth_{withdrawal_req.id}_{secrets.token_hex(3)}"

    # Sandbox Simulation Fallback
    if is_sandbox:
        simulated_utr = f"UTR_SIM_{secrets.token_hex(6).upper()}"
        logger.info("Sandbox Mode: Simulated payout of ₹%s disbursed successfully. UTR: %s", withdrawal_req.amount, simulated_utr)
        return True, simulated_utr, "Sandbox payout simulated successfully."

    # Live RazorpayX API Payload
    fund_account_payload = {
        "account_number": rx_account_number,
        "amount": amount_paise,
        "currency": "INR",
        "purpose": "payout",
        "queue_if_low_balance": True,
        "reference_id": ref_id,
    }

    if withdrawal_req.payout_mode == "UPI":
        fund_account_payload["mode"] = "UPI"
        fund_account_payload["fund_account"] = {
            "account_type": "vpa",
            "vpa": {"address": withdrawal_req.upi_id},
            "contact": {
                "name": specialist.name,
                "email": specialist.email,
                "type": "employee"
            }
        }
    else:
        fund_account_payload["mode"] = "NEFT"
        fund_account_payload["fund_account"] = {
            "account_type": "bank_account",
            "bank_account": {
                "name": withdrawal_req.bank_beneficiary_name or specialist.name,
                "ifsc": withdrawal_req.bank_ifsc,
                "account_number": withdrawal_req.bank_account_number
            },
            "contact": {
                "name": specialist.name,
                "email": specialist.email,
                "type": "employee"
            }
        }

    try:
        url = "https://api.razorpay.com/v1/payouts"
        data_bytes = json.dumps(fund_account_payload).encode("utf-8")
        
        auth_str = f"{key_id}:{key_secret}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {b64_auth}"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            payout_id = res_data.get("id", "POUT_UNKNOWN")
            utr = res_data.get("utr") or res_data.get("reference_id") or payout_id
            return True, str(utr), f"Disbursed via RazorpayX (ID: {payout_id})"
    except Exception as exc:
        logger.exception("RazorpayX Payout API error: %s", exc)
        return False, "", str(exc)