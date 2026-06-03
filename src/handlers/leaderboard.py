"""
Leaderboard handler — triggered automatically on the 1st of every month.
Fetches last month scores per space, posts leaderboard to each space independently,
picks a weighted raffle winner per space (excluding last month's winner for that space
to prevent back-to-back wins). Spaces with no activity are silently skipped.
"""

import logging
import os
import random
from datetime import date

from services.config import get_config
from services.points import get_last_raffle_winner, get_monthly_scores, save_raffle_winner
from services.utils import today_et
from services.webex import get_display_name, get_room_name, send_message

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    config = get_config()
    token = config["bot_token"]
    table_name = os.environ["SCORES_TABLE"]

    today = today_et()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    default_period = f"{year}-{month:02d}"

    # Allow manual invocation with a specific period e.g. {"period": "2026-04"}
    period = event.get("period", default_period)

    try:
        p_year, p_month = map(int, period.split("-"))
        month_name = date(p_year, p_month, 1).strftime("%B %Y")
    except (ValueError, TypeError):
        logger.error("Invalid period format: %s — expected YYYY-MM", period)
        return {"status": "error", "reason": "invalid period, use YYYY-MM"}

    logger.info("Generating leaderboard for %s across %d space(s)", period, len(config.get("allowed_space_ids", [])))

    results = []
    for space_id in config.get("allowed_space_ids", []):
        try:
            _process_space(space_id, period, month_name, token, table_name, results)
        except Exception as exc:
            logger.exception("Unhandled error processing space %s: %s", space_id, exc)
            results.append({"space_id": space_id, "status": "error"})

    posted = [r for r in results if r.get("status") == "posted"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    logger.info("Leaderboard run complete — posted: %d, skipped (no scores): %d", len(posted), len(skipped))

    return {"status": "done", "period": period, "spaces": results}


def _process_space(space_id: str, period: str, month_name: str, token: str, table_name: str, results: list) -> None:
    """Run the full leaderboard + raffle flow for a single space."""
    scores = get_monthly_scores(period, space_id, table_name)

    if not scores:
        logger.info("No scores for space %s in %s — skipping", space_id, period)
        results.append({"space_id": space_id, "status": "skipped"})
        return

    space_name = get_room_name(space_id, token)
    scores.sort(key=lambda x: x["points"], reverse=True)

    # ── Build leaderboard message ─────────────────────────────────────────────
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 **Pulse Leaderboard — {space_name} — {month_name}**\n"]
    for i, entry in enumerate(scores):
        name = entry.get("user_name") or get_display_name(entry["person_id"], token)
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} **{name}** — {entry['points']} point{'s' if entry['points'] != 1 else ''}")

    # ── Raffle: weighted by points, exclude last month's winner for this space ─
    last_winner_id = get_last_raffle_winner(table_name, room_id=space_id)
    eligible = [e for e in scores if e["person_id"] != last_winner_id]

    if not eligible:
        # Edge case: only 1 person participated and they won last month too
        eligible = scores

    # Each point = 1 raffle ticket
    pool = []
    for entry in eligible:
        pool.extend([entry["person_id"]] * entry["points"])

    winner_id = random.choice(pool)
    winner_name = get_display_name(winner_id, token)
    save_raffle_winner(
        winner_id,
        period,
        room_id=space_id,
        table_name=table_name,
        winner_name=winner_name,
        space_name=space_name,
    )

    lines.append(f"\n🎉 **Raffle Winner: {winner_name}!** 🎊")
    lines.append("_Winner chosen by weighted random draw — more points = more tickets!_")

    send_message(space_id, "\n".join(lines), token)
    logger.info("Posted leaderboard to space '%s' (%s) — %d entries, winner: %s", space_name, space_id, len(scores), winner_name)
    results.append({"space_id": space_id, "space_name": space_name, "status": "posted", "winner": winner_name, "entries": len(scores)})