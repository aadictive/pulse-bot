"""
Leaderboard handler — triggered automatically on the 1st of every month.
Fetches last month's scores from DynamoDB, posts a leaderboard to all
configured Webex spaces, and picks a random raffle winner.
"""

import json
import logging
import os
import random
from datetime import date

import boto3

from services.config import get_config
from services.points import get_monthly_scores
from services.webex import get_display_name, send_message

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    """Entry point called by EventBridge on a schedule."""
    config = get_config()

    # We want last month's scores (this runs on the 1st of the new month)
    today = date.today()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    period = f"{year}-{month:02d}"
    logger.info("Generating leaderboard for period: %s", period)

    scores = get_monthly_scores(period, os.environ["SCORES_TABLE"])

    if not scores:
        logger.info("No scores found for %s — skipping leaderboard", period)
        return {"status": "no scores"}

    # Sort highest to lowest
    scores.sort(key=lambda x: x["points"], reverse=True)

    # Build leaderboard message
    month_name = date(year, month, 1).strftime("%B %Y")
    lines = [f"🏆 **Pulse Leaderboard — {month_name}**\n"]

    medals = ["🥇", "🥈", "🥉"]
    for i, entry in enumerate(scores):
        display_name = get_display_name(entry["person_id"], config["bot_token"])
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} **{display_name}** — {entry['points']} point{'s' if entry['points'] != 1 else ''}")

    # ── Raffle: weighted by points (more points = more tickets) ──────────────
    pool = []
    for entry in scores:
        pool.extend([entry["person_id"]] * entry["points"])

    winner_id = random.choice(pool)
    winner_name = get_display_name(winner_id, config["bot_token"])
    lines.append(f"\n🎉 **Raffle Winner: {winner_name}!** 🎊")
    lines.append("_Winner was chosen randomly — more points = more raffle tickets!_")

    message = "\n".join(lines)

    # Post to all allowed spaces
    for space_id in config.get("allowed_space_ids", []):
        send_message(space_id, message, config["bot_token"])
        logger.info("Posted leaderboard to space: %s", space_id)

    return {"status": "posted", "period": period, "entries": len(scores)}
