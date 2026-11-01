# Save this directly into your file: app/services/whatsapp.py

import os
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

# Standard Meta WhatsApp Cloud API Endpoint or Indian BSPs (Wati, Aisensy, Interakt)
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"

def _format_phone(phone):
    """Normalize Indian phone numbers to international E.164 format (e.g. 919876543210)."""
    if not phone:
        return None
    cleaned = "".join(filter(str.isdigit, str(phone)))
    if len(cleaned) == 10:
        return f"91{cleaned}"
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return cleaned
    return cleaned

def send_whatsapp_alert(to_phone, message_text):
    """Dispatch WhatsApp message via Meta Cloud API or log safely if in development."""
    formatted_number = _format_phone(to_phone)
    if not formatted_number:
        logger.warning("WhatsApp alert skipped: invalid or missing phone number.")
        return False

    token = os.getenv("WHATSAPP_API_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_ID")

    # Safe fallback: Log message if API keys are not yet configured
    if not token or not phone_id:
        logger.info(f"[WHATSAPP MOCK DISPATCH] To: {formatted_number} | Message: {message_text}")
        return True

    url = f"{WHATSAPP_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": formatted_number,
        "type": "text",
        "text": {"body": message_text}
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code in (200, 201):
            logger.info(f"WhatsApp message dispatched successfully to {formatted_number}")
            return True
        else:
            logger.error(f"WhatsApp API error {res.status_code}: {res.text}")
            return False
    except Exception as exc:
        logger.error(f"WhatsApp delivery network failure: {str(exc)}")
        return False


# =========================================================
# PRE-BUILT AGENCY BUSINESS NOTIFICATION TEMPLATES
# =========================================================

def notify_payment_received(project_id, service_name, amount, client_phone, client_name):
    """Send payment confirmation to client."""
    msg = (
        f"✅ *Payment Received — AgencyOS*\n\n"
        f"Hi {client_name},\n"
        f"We have received your advance deposit of *₹{amount:,.2f}* for Project #{project_id} ({service_name}).\n\n"
        f"Milestone 1 is now active! Our specialists have begun discovery & wireframing.\n"
        f"Track progress in your workspace: https://yourdomain.com/project/{project_id}"
    )
    return send_whatsapp_alert(client_phone, msg)

def notify_deliverable_ready(project_id, service_name, preview_url, client_phone, client_name):
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

def notify_revision_requested(project_id, service_name, specialist_phone, specialist_name, comment, round_num):
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

def notify_project_completed(project_id, service_name, client_phone, client_name):
    """Congratulate client on live deployment and IP handover."""
    msg = (
        f"🎉 *Project Successfully Handed Over — AgencyOS*\n\n"
        f"Hi {client_name},\n"
        f"All milestone deliverables for *{service_name}* (Project #{project_id}) have been approved and deployed!\n\n"
        f"Download your tax invoice: https://yourdomain.com/project/{project_id}/invoice\n"
        f"Thank you for building with AgencyOS!"
    )
    return send_whatsapp_alert(client_phone, msg)