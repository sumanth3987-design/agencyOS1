"""Meta Cloud API & BSP WhatsApp Notification Engine for AgencyOS."""

import logging
import os
from typing import Any
import requests
from flask import current_app

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"


def _format_phone(phone: str | None) -> str | None:
    """Normalize phone numbers to international E.164 format."""
    if not phone:
        return None
    # Strip non-digits
    cleaned = "".join(filter(str.isdigit, str(phone)))
    
    # If it's your 10-digit number, prepend 91
    if len(cleaned) == 10:
        return f"91{cleaned}"
    # If it already has 91, return it
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return cleaned
    return cleaned

def send_whatsapp_alert(to_phone: str | None, message_text: str) -> bool:
    """Dispatch WhatsApp message via Meta Cloud API or log safely if in development."""
    formatted_number = _format_phone(to_phone)
    if not formatted_number:
        logger.warning("WhatsApp alert skipped: invalid or missing phone number.")
        return False

    token = os.getenv("WHATSAPP_API_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_ID")

    if not token or not phone_id:
        logger.info(f"[WHATSAPP MOCK DISPATCH] To: {formatted_number} | Message: {message_text}")
        return True

    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_number,
        "type": "text",
        "text": {"body": message_text},
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code in (200, 201):
            logger.info("WhatsApp message dispatched successfully to %s", formatted_number)
            return True
        else:
            logger.error("WhatsApp API error %s: %s", res.status_code, res.text)
            return False
    except Exception as exc:
        logger.error("WhatsApp delivery network failure: %s", str(exc))
        return False


def notify_payment_received(project_id: int, service_name: str, amount: Any, client_phone: str | None, client_name: str) -> bool:
    """Send payment confirmation to client."""
    msg = (
        f"✅ *Payment Received — AgencyOS*\n\n"
        f"Hi {client_name},\n"
        f"We have received your advance deposit of *₹{amount:,.2f}* for Project #{project_id} ({service_name}).\n\n"
        f"Milestone 1 is now active! Our specialists have begun discovery & wireframing.\n"
        f"Track progress in your workspace: https://yourdomain.com/project/{project_id}"
    )
    return send_whatsapp_alert(client_phone, msg)


def notify_deliverable_ready(project_id: int, service_name: str, preview_url: str, client_phone: str | None, client_name: str) -> bool:
    """Notify client that a new deliverable staging link is ready for review."""
    msg = (
        f"🚀 *Deliverable Ready for Review — Project #{project_id}*\n\n"
        f"Hi {client_name},\n"
        f"A new milestone deliverable for *{service_name}* has been submitted by your team.\n\n"
        f"🔗 *Preview Link:* {preview_url}\n\n"
        f"Please inspect the staging link and approve or submit revision feedback inside your workspace: "
        f"https://yourdomain.com/project/{project_id}"
    )
    return send_whatsapp_alert(client_phone, msg)


def notify_revision_requested(project_id: int, service_name: str, specialist_phone: str | None, specialist_name: str, comment: str, round_num: int) -> bool:
    """Alert specialist that client submitted revision feedback."""
    msg = (
        f"⚠️ *Revision Feedback (Round {round_num}/2) — Sprint #{project_id}*\n\n"
        f"Hi {specialist_name},\n"
        f"The client has reviewed deliverable for *{service_name}* and requested adjustments:\n\n"
        f"💬 *Notes:* \"{comment}\"\n\n"
        f"Open delivery workspace to submit adjustments: "
        f"https://yourdomain.com/professional/project/{project_id}/workspace"
    )
    return send_whatsapp_alert(specialist_phone, msg)


def notify_project_completed(project_id: int, service_name: str, client_phone: str | None, client_name: str) -> bool:
    """Congratulate client on live deployment and IP handover."""
    msg = (
        f"🎉 *Project Successfully Handed Over — AgencyOS*\n\n"
        f"Hi {client_name},\n"
        f"All milestone deliverables for *{service_name}* (Project #{project_id}) have been approved and deployed!\n\n"
        f"Download your tax invoice: https://yourdomain.com/project/{project_id}/invoice\n"
        f"Thank you for building with AgencyOS!"
    )
    return send_whatsapp_alert(client_phone, msg)
