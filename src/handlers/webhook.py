"""
Webhook handler — receives all messages from Webex via API Gateway.
Validates the request came from an allowed space, parses the mention,
and awards a point to the tagged user.

Commands (all require @Pulse mention):
  @Pulse @Name            — give 1 point (everyone)
  @Pulse scores           — show this month's leaderboard (everyone)
  @Pulse help             — show available commands (everyone)
  @Pulse @Name --         — remove 1 point (admins only)
  @Pulse @Name 2026-04-26 — give a point for a specific date (admins only)
"""

import json
import logging
import os
import re
from datetime import date, datetime

from services.config import get_config
from services.points import award_point, get_monthly_scores, remove_point
from services.webex import get_display_name, get_message_details, send_message

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Matches a date like 2026-04-26 anywhere in the message text
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


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

        clean_text = text.strip()
        is_admin = sender_person_id in config.get("admin_person_ids", [])

        # ── scores command: "@Pulse scores" ──────────────────────────────────
        if re.search(r"\bscores\b", clean_text, re.IGNORECASE):
            _post_scores(room_id, config)
            return _ok("scores posted")

        # ── help command: "@Pulse help" ───────────────────────────────────────
        if re.search(r"\bhelp\b", clean_text, re.IGNORECASE):
            _post_help(room_id, config, is_admin)
            return _ok("help posted")

        # ── Point commands — need at least one person mentioned besides the bot
        recipients = [pid for pid in mentioned_people if pid != bot_person_id]

        if not recipients:
            send_message(
                room_id,
                "👋 Mention someone to give them a point! e.g. **@Pulse @Aditya**\n"
                "Type **@Pulse help** for all commands.",
                config["bot_token"],
            )
            return _ok("no recipients")

        # ── remove command: "@Pulse @Name --" (admin only) ───────────────────
        if "--" in clean_text:
            if not is_admin:
                send_message(
                    room_id,
                    f"🚫 Only admins can remove points. Ask {_admin_mentions(config)}",
                    config["bot_token"],
                )
                return _ok("unauthorised remove")
            responses = []
            for person_id in recipients:
                result = remove_point(
                    recipient_person_id=person_id,
                    table_name=os.environ["SCORES_TABLE"],
                )
                responses.append(result)
            send_message(room_id, "\n".join(r["message"] for r in responses), config["bot_token"])
            return _ok("points removed")

        # ── backdate command: "@Pulse @Name 2026-04-26" (admin only) ─────────
        date_match = _DATE_RE.search(clean_text)
        override_date = None
        if date_match:
            if not is_admin:
                send_message(
                    room_id,
                    f"🚫 Only admins can backdate points. Ask {_admin_mentions(config)}",
                    config["bot_token"],
                )
                return _ok("unauthorised backdate")
            try:
                parsed = date.fromisoformat(date_match.group(1))
                if parsed > date.today():
                    send_message(room_id, "🚫 Cannot award points for a future date.", config["bot_token"])
                    return _ok("future date rejected")
                override_date = parsed.isoformat()
            except ValueError:
                send_message(room_id, "⚠️ Invalid date format. Use YYYY-MM-DD.", config["bot_token"])
                return _ok("invalid date")

        # ── award point(s): "@Pulse @Name" or "@Pulse @Name 2026-04-26" ──────
        responses = []
        for person_id in recipients:
            result = award_point(
                giver_person_id=sender_person_id,
                recipient_person_id=person_id,
                room_id=room_id,
                table_name=os.environ["SCORES_TABLE"],
                override_date=override_date,
            )
            responses.append(result)

        send_message(room_id, "\n".join(r["message"] for r in responses), config["bot_token"])
        return _ok("points awarded")

    except Exception as exc:
        logger.exception("Unhandled error in webhook handler: %s", exc)
        # Always return 200 to Webex — otherwise it retries forever
        return _ok("internal error")


def _admin_mentions(config: dict) -> str:
    """Return a string that tags all admins e.g. '<@personId:X> <@personId:Y>'."""
    return " ".join(
        f"<@personId:{pid}>" for pid in config.get("admin_person_ids", [])
    )


def _post_scores(room_id: str, config: dict) -> None:
    """Fetch current month's scores and post a mini leaderboard to the space."""
    period = date.today().strftime("%Y-%m")
    month_name = date.today().strftime("%B %Y")
    scores = get_monthly_scores(period, os.environ["SCORES_TABLE"])

    if not scores:
        send_message(room_id, f"📊 No points awarded yet in {month_name}!", config["bot_token"])
        return

    scores.sort(key=lambda x: x["points"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"📊 **Pulse Scores — {month_name}**\n"]
    for i, entry in enumerate(scores):
        name = get_display_name(entry["person_id"], config["bot_token"])
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} **{name}** — {entry['points']} point{'s' if entry['points'] != 1 else ''}")

    send_message(room_id, "\n".join(lines), config["bot_token"])


def _post_help(room_id: str, config: dict, is_admin: bool) -> None:
    """Post available commands. Admins see extra commands."""
    lines = [
        "👋 **Pulse Bot Commands**\n",
        "• **@Pulse @Name** — give someone 1 point _(1 per person per day)_",
        "• **@Pulse scores** — see this month's leaderboard",
        "• **@Pulse help** — show this message",
        "\n_Rules: No self-points. Leaderboard + raffle posted automatically on the 1st of each month._",
    ]
    if is_admin:
        lines.insert(-1, "\n🔧 **Admin Commands**")
        lines.insert(-1, "• **@Pulse @Name --** — remove 1 point from someone")
        lines.insert(-1, "• **@Pulse @Name 2026-04-26** — backdate a point to a specific date")

    send_message(room_id, "\n".join(lines), config["bot_token"])


def _ok(reason: str) -> dict:
    return {"statusCode": 200, "body": json.dumps({"status": reason})}

