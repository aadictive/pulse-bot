"""
Webhook handler — receives all messages from Webex via API Gateway.
Validates the request came from an allowed space, parses the mention,
and awards a point to the tagged user.
"""

import json
import logging
import os
import re

import boto3

from services.config import get_config
from services.points import award_point
from services.webex import get_message_details, send_message

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    """Entry point called by API Gateway for every Webex webhook event."""
    try:
        body = json.loads(event.get("body", "{}"))
        logger.info("Received webhook: %s", json.dumps(body))

        # Webex sends a "ping" when you register the webhook — just acknowledge it
        if body.get("resource") != "messages" or body.get("event") != "created":
            return _ok("ignored non-message event")

        room_id = body.get("data", {}).get("roomId", "")
        message_id = body.get("data", {}).get("id", "")
        sender_person_id = body.get("data", {}).get("personId", "")

        # ── Security: only process messages from allowed spaces ──────────────
        config = get_config()
        allowed_spaces = config.get("allowed_space_ids", [])
        if room_id not in allowed_spaces:
            logger.warning("Message from unauthorised space: %s", room_id)
            return _ok("space not allowed")  # return 200 so Webex stops retrying

        # ── Fetch full message text (webhook payload only has metadata) ───────
        message = get_message_details(message_id, config["bot_token"])
        if not message:
            return _ok("could not fetch message")

        text = message.get("text", "")
        mentioned_people = message.get("mentionedPeople", [])

        bot_person_id = config["bot_person_id"]

        # Ignore messages not directed at the bot
        if bot_person_id not in mentioned_people:
            return _ok("bot not mentioned")

        # Ignore the bot talking to itself
        if sender_person_id == bot_person_id:
            return _ok("bot's own message")

        # ── Parse: "@Pulse @SomeName" ─────────────────────────────────────────
        # mentionedPeople contains person IDs; we also parse display names from text
        # Remove the bot's own mention, then award points to everyone else mentioned
        recipients = [pid for pid in mentioned_people if pid != bot_person_id]

        if not recipients:
            send_message(
                room_id,
                "👋 Mention someone to give them a point! e.g. **@Pulse @Aditya**",
                config["bot_token"],
            )
            return _ok("no recipients")

        # Award a point to each mentioned person
        responses = []
        for person_id in recipients:
            result = award_point(
                giver_person_id=sender_person_id,
                recipient_person_id=person_id,
                room_id=room_id,
                table_name=os.environ["SCORES_TABLE"],
            )
            responses.append(result)

        # Build a single reply message
        reply_lines = [r["message"] for r in responses]
        send_message(room_id, "\n".join(reply_lines), config["bot_token"])

        return _ok("points awarded")

    except Exception as exc:
        logger.exception("Unhandled error in webhook handler: %s", exc)
        # Always return 200 to Webex — otherwise it retries forever
        return _ok("internal error")


def _ok(reason: str) -> dict:
    return {"statusCode": 200, "body": json.dumps({"status": reason})}
