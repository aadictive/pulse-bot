"""
Webex API service — thin wrapper around the Webex REST API.

Two sessions are maintained:
- _SESSION  : retries up to 3 times with exponential back-off (for normal calls)
- _DIRECT_SESSION : single attempt, no retries (for best-effort fallback messages
                    after the retry budget has already been spent)
"""

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

WEBEX_API = "https://webexapis.com/v1"

# Retry up to 3 times on 429/503/504 with exponential back-off (1s, 2s, 4s).
# Per-request timeout is 10 s; worst-case budget: 4×10s + 7s backoff ≈ 47 s.
# Lambda timeout must remain above this (currently 120 s).
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

# No retry adapter — used only for one-shot fallback messages so we don't burn
# the remaining Lambda budget on a second full retry cycle.
_DIRECT_SESSION = requests.Session()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_message_details(message_id: str, token: str) -> dict | None:
    """Fetch the full message object (webhook payload only has metadata)."""
    try:
        resp = _SESSION.get(
            f"{WEBEX_API}/messages/{message_id}",
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.exception("Failed to fetch message %s: %s", message_id, exc)
        return None


def send_message(room_id: str, text: str, token: str) -> bool:
    """Post a markdown message to a Webex space (with retry on transient errors)."""
    try:
        resp = _SESSION.post(
            f"{WEBEX_API}/messages",
            headers=_headers(token),
            json={"roomId": room_id, "markdown": text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.exception("Failed to send message to room %s: %s", room_id, exc)
        return False


def send_message_once(room_id: str, text: str, token: str) -> bool:
    """Post a markdown message — single attempt, no retries.

    Use this for best-effort fallback notifications (e.g. after get_message_details
    has already exhausted its retry budget) so Lambda still has time to return.
    """
    try:
        resp = _DIRECT_SESSION.post(
            f"{WEBEX_API}/messages",
            headers=_headers(token),
            json={"roomId": room_id, "markdown": text},
            timeout=8,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.exception("Fallback send_message failed for room %s: %s", room_id, exc)
        return False


def get_display_name(person_id: str, token: str) -> str:
    """Look up a person's display name by their Webex person ID."""
    try:
        resp = _SESSION.get(
            f"{WEBEX_API}/people/{person_id}",
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("displayName") or data.get("firstName", person_id)
    except Exception as exc:
        logger.exception("Failed to get display name for %s: %s", person_id, exc)
        return person_id   # fallback to raw ID
