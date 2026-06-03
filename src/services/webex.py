"""
Webex API service — thin wrapper around the Webex REST API.

Two sessions are maintained:
- _SESSION  : retries on HTTP error status codes (503, 429, 504) but NOT on
              connection failures — failing fast on connect errors means Lambda
              still has budget to send a fallback notification.
- _DIRECT_SESSION : single attempt, no retries (for best-effort fallback messages
                    after the main call has already failed)

Why connect=0?
  During a Webex outage each TCP/SSL connection attempt takes ~30s at the OS
  level regardless of the per-request timeout setting. With 3 retries that
  would be 4 × 30s = 120s — exactly the Lambda timeout — and the fallback
  message never sends. Setting connect=0 means we give up after one attempt
  (~30s), leaving ~90s for the fallback notification and a clean return.
  Status-code retries (503/429) are unaffected because those require a
  successful connection first.
"""

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

WEBEX_API = "https://webexapis.com/v1"

# Status-code retries only — do NOT retry connection failures.
# connect=0  → fail immediately on ConnectTimeoutError / connection refused
# total=3    → up to 3 retries when we get a 429/503/504 HTTP response
# Worst-case with a status retry: 4 attempts × 10s + (1+2+4)s backoff ≈ 47s
_RETRY = Retry(
    total=3,
    connect=0,          # ← fail fast on connection errors (outage scenario)
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
