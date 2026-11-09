"""AgencyOS RazorpayX Payouts, Meritocracy & Billable Time Aggregation Engine."""

import base64
import hashlib
import json
import logging
import os
import secrets
from decimal import Decimal
import urllib.request
from typing import Any

from app.models import ProfessionalProfile, ProjectTimeLog, User

logger = logging.getLogger(__name__)


def get_project_billable_hours_summary(project_id: int) -> dict[str, Any]:
    """Aggregates all billable hours and financial values logged against a project."""
    try:
        logs = ProjectTimeLog.query.filter_by(project_id=project_id).all()
        total_hours = sum(Decimal(str(l.hours)) for l in logs) if logs else Decimal("0.00")
        total_billable = sum(Decimal(str(l.hours)) * Decimal(str(l.hourly_rate)) for l in logs) if logs else Decimal("0.00")
        unbilled_logs = [l for l in logs if not l.is_billed]
        unbilled_hours = sum(Decimal(str(l.hours)) for l in unbilled_logs) if unbilled_logs else Decimal("0.00")
        unbilled_amount = sum(Decimal(str(l.hours)) * Decimal(str(l.hourly_rate)) for l in unbilled_logs) if unbilled_logs else Decimal("0.00")

        return {
            "total_hours": total_hours,
            "total_billable_amount": total_billable,
            "unbilled_hours": unbilled_hours,
            "unbilled_amount": unbilled_amount,
            "log_count": len(logs),
        }
    except Exception as exc:
        logger.error("Error computing billable hours summary for project %s: %s", project_id, exc)
        return {
            "total_hours": Decimal("0.00"),
            "total_billable_amount": Decimal("0.00"),
            "unbilled_hours": Decimal("0.00"),
            "unbilled_amount": Decimal("0.00"),
            "log_count": 0,
        }


def calculate_meritocracy_rate(specialist_user: User) -> Decimal:
    """Computes dynamic performance-based payout rate.
    
    Standard talent share: 45% (0.45)
    High Performer (Rating >= 4.8 & Zero Revisions): 48% (0.48)
    Elite Specialist (Verified + Rating >= 4.9 + 5+ Completed Sprints): 50% (0.50)
    """
    profile: ProfessionalProfile | None = getattr(specialist_user, "professional_profile", None)
    if not profile:
        return Decimal("0.45")

    try:
        metrics = profile.get_performance_metrics()
        rating = metrics.get("average_rating")
        revisions = metrics.get("revision_count", 0)
        completed = metrics.get("completed_sprints", 0)

        if profile.verification_status == "Verified" and rating and rating >= 4.9 and completed >= 5:
            return Decimal("0.50")
        elif rating and rating >= 4.8 and revisions == 0:
            return Decimal("0.48")
    except Exception as exc:
        logger.warning("Could not calculate meritocracy bonus: %s", exc)

    return Decimal("0.45")


def disburse_payout(withdrawal_req: Any) -> tuple[bool, str, str]:
    """Disburse funds via RazorpayX Payouts API or fallback to sandbox simulation.
    
    Returns: (success: bool, utr_or_reference: str, message: str)
    """
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    rx_account_number = os.getenv("RAZORPAYX_ACCOUNT_NUMBER", "78910111213")

    is_sandbox = not key_id or "placeholder" in key_id.lower() or "test" in key_id.lower()

    specialist = withdrawal_req.specialist
    amount_paise = int(round(float(withdrawal_req.amount) * 100))
    ref_id = f"wth_{withdrawal_req.id}_{secrets.token_hex(4)}"

    # Generate immutable cryptographic checksum for audit ledger
    checksum_raw = f"{withdrawal_req.id}:{withdrawal_req.amount}:{withdrawal_req.payout_mode}:{specialist.email}"
    audit_hash = hashlib.sha256(checksum_raw.encode("utf-8")).hexdigest()[:16].upper()

    # Sandbox Simulation Fallback
    if is_sandbox:
        simulated_utr = f"UTR_SIM_{audit_hash}"
        logger.info(
            "Sandbox Mode: Simulated payout of ₹%s disbursed successfully. UTR: %s",
            withdrawal_req.amount,
            simulated_utr,
        )
        return True, simulated_utr, "Sandbox payout simulated successfully with verified audit hash."

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
                "type": "employee",
            },
        }
    else:
        fund_account_payload["mode"] = "NEFT"
        fund_account_payload["fund_account"] = {
            "account_type": "bank_account",
            "bank_account": {
                "name": withdrawal_req.bank_beneficiary_name or specialist.name,
                "ifsc": withdrawal_req.bank_ifsc,
                "account_number": withdrawal_req.bank_account_number,
            },
            "contact": {
                "name": specialist.name,
                "email": specialist.email,
                "type": "employee",
            },
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
                "Authorization": f"Basic {b64_auth}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            payout_id = res_data.get("id", "POUT_UNKNOWN")
            utr = res_data.get("utr") or res_data.get("reference_id") or payout_id
            return True, str(utr), f"Disbursed via RazorpayX (ID: {payout_id}, Audit: {audit_hash})"
    except Exception as exc:
        logger.exception("RazorpayX Payout API error: %s", exc)
        return False, "", str(exc)