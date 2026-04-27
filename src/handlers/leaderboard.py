"""
Leaderboard handler — triggered automatically on the 1st of every month.
Fetches last month scores, posts leaderboard, picks a weighted raffle winner
(excluding last month winner to prevent back-to-back wins).
"""

import logging
import os
import random
from datetime import date

from services.config import get_config
from services.points import get_last_raffle_winner, get_monthly_scores, save_raffle_winner
from services.webex import get_display_name, send_message

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    config = get_config()

    today = date.today()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    period = f"{year}-{month:02d}"
    month_name = date(year, month, 1).strftime("%B %Y")
    logger.info("Generating leaderboard for %s", period)

    scores = get_monthly_scores(period, os.environ["SCORES_TABLE"])
    if not scores:
        logger.info("No scores for %s — skipping", period)
        return {"status": "no scores"}

    scores.sort(key=lambda x: x["points"], reverse=True)

    # ── Build leaderboard message ─────────────────────────────────────────────
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 **Pulse Leaderboard — {month_name}**\n"]
    for i, entry in enumerate(scores):
        name = get_display_name(entry["person_id"], config["bot_token"])
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} **{name}** — {entry['points']} point{'s' if entry['points'] != 1 else ''}")

    # ── Raffle: weighted by points, exclude last month winner ─────────────────
    last_winner_id = get_last_raffle_winner(os.environ["SCORES_TABLE"])
    eligible = [e for e in scores if e["person_id"] != last_winner_id]

    if not eligible:
        # Edge case: only 1 person participated and they won last month too
        eligible = scores

    # Build weighted pool: each point = 1 ticket
    pool = []
    for entry in eligible:
        pool.extend([entry["person_id"]] * entry["points"])

    winner_id = random.choice(pool)
    winner_name = get_display_name(winner_id, config["bot_token"])
    save_raffle_winner(winner_id, period, os.environ["SCORES_TABLE"])

    if last_winner_id and last_winner_id != winner_id:
        last_winner_name = get_display_name(last_winner_id, config["bot_token"])
        lines.append(f"\n_({last_winner_name} was excluded from this month's raffle as last month's winner)_")

    lines.append(f"\n🎉 **Raffle Winner: {winner_name}!** 🎊")
    lines.append("_Winner chosen by weighted random draw — more points = more tickets!_")

    message = "\n".join(lines)
    for space_id in config.get("allowed_space_ids", []):
        send_message(space_id, message, config["bot_token"])
        logger.info("Posted leaderboard to space %s", space_id)

    return {"status": "posted", "period": period, "winner": winner_name, "entries": len(scores)}