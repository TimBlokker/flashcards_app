"""Minimal WhatsApp integration.

Two delivery paths:

* ``whatsapp_deep_link`` builds a ``wa.me`` link that opens WhatsApp with the
  reminder already typed. It needs no credentials and is used by the
  "remind me now" button.
* ``send_whatsapp_message`` posts to the WhatsApp Cloud API and is used by the
  ``send_study_reminders`` management command. It needs an access token and a
  phone number id, taken from Django settings or the environment.
"""
import json
import os
from urllib import parse, request

from django.conf import settings

GRAPH_API_VERSION = "v19.0"


class WhatsAppNotConfigured(RuntimeError):
    pass


def whatsapp_config():
    token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "") or os.environ.get(
        "WHATSAPP_ACCESS_TOKEN",
        "",
    )
    phone_number_id = getattr(
        settings,
        "WHATSAPP_PHONE_NUMBER_ID",
        "",
    ) or os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    return token, phone_number_id


def whatsapp_deep_link(phone_number, message):
    digits = "".join(character for character in phone_number if character.isdigit())
    return f"https://wa.me/{digits}?text={parse.quote(message)}"


def send_whatsapp_message(phone_number, body):
    """Send one text message through the WhatsApp Cloud API."""
    token, phone_number_id = whatsapp_config()
    if not token or not phone_number_id:
        raise WhatsAppNotConfigured(
            "Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID "
            "to send automatic reminders."
        )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number.lstrip("+"),
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    http_request = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(http_request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def app_url():
    return getattr(settings, "STUDY_APP_URL", "") or os.environ.get(
        "STUDY_APP_URL",
        "",
    )
