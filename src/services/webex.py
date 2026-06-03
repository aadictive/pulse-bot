"""
Webex API service — thin wrapper around the Webex REST API.
All calls retry up to 3 times with exponential back-off on transient
errors (503, 429, timeouts) before giving up.
"""

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

WEBEX_API = "https://webexapis.com/v1"

# Retry up to 3 times on 429/503/504 with exponential back-off (1s, 2s, 4s)
_RETRY = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 503, 504],
    allowed_methods=["GET", "POST"],
    raise_on_status=False,
)
_ADAPTER = HTTPAdapter(max_retries=_RETRY)
_SESSION = requests.Session()
_SESSION.mount("https://", _ADAPTER)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_message_details(message_id: str, token: str) -> dict | None:
    """Fetch the full message object (webhook payload only has metadata)."""
    try:
        resp = _SESSION.get(
            f"{WEBEX_API}/messages/{message_id}",
            headers=_headers(token),
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.exception("Failed to fetch message %s: %s", message_id, exc)
        return None


def send_message(room_id: str, text: str, token: str) -> bool:
    """Post a markdown message to a Webex space."""
    try:
        resp = _SESSION.post(
            f"{WEBEX_API}/messages",
            headers=_headers(token),
            json={"roomId": room_id, "markdown": text},
            timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.exception("Failed to send message to room %s: %s", room_id, exc)
        return False


def get_display_name(person_id: str, token: str) -> str:
    """Look up a person's display name by their Webex person ID."""
    try:
        resp = _SESSION.get(
            f"{WEBEX_API}/people/{person_id}",
            headers=_headers(token),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("displayName") or data.get("firstName", person_id)
    except Exception as exc:
        logger.exception("Failed to get display name for %s: %s", person_id, exc)
        return person_id   # fallback to raw ID
