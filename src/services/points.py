"""
Points service — reads and writes scores to DynamoDB.

Table schema:
  pk  = "YYYY-MM"          (partition key — the month)
  sk  = "user#<personId>"  (sort key — the person)
  points     = int
  last_given = "YYYY-MM-DD" (date of last point — for daily dedup)
  display_name = str        (cached for leaderboard display)
  ttl = epoch timestamp     (auto-delete after 13 months)
"""

import logging
import os
from datetime import date, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _table(table_name: str):
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return dynamodb.Table(table_name)


def award_point(
    giver_person_id: str,
    recipient_person_id: str,
    room_id: str,
    table_name: str,
    override_date: str = None,   # admin-only: "YYYY-MM-DD"
) -> dict:
    """
    Give recipient_person_id 1 point.
    Rules:
      - A person cannot give points to themselves.
      - Only 1 point per recipient per day (unless override_date is provided by admin).
    Returns a dict with a human-readable 'message'.
    """
    target_date = date.fromisoformat(override_date) if override_date else date.today()
    period = target_date.strftime("%Y-%m")
    target_str = target_date.isoformat()

    # ── Rule: no self-points ──────────────────────────────────────────────────
    if giver_person_id == recipient_person_id:
        return {"success": False, "message": "🚫 You can't give yourself a point, cheeky! 😄"}

    table = _table(table_name)
    sk = f"user#{recipient_person_id}"

    try:
        existing = table.get_item(Key={"pk": period, "sk": sk}).get("Item")
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return {"success": False, "message": "⚠️ Something went wrong. Try again later."}

    # ── Rule: only 1 point per person per day (skip if admin override) ───────
    if not override_date and existing and existing.get("last_given") == target_str:
        current = existing.get("points", 0)
        return {
            "success": False,
            "message": (
                f"⏳ <@personId:{recipient_person_id}> already received a point today! "
                f"They have **{current}** point{'s' if current != 1 else ''} this month."
            ),
        }

    # TTL: keep data for 13 months then auto-delete
    import time as _time
    ttl = int(_time.time()) + (13 * 30 * 24 * 60 * 60)

    try:
        response = table.update_item(
            Key={"pk": period, "sk": sk},
            UpdateExpression=(
                "SET points = if_not_exists(points, :zero) + :one, "
                "last_given = :today, "
                "person_id = :pid, "
                "#ttl = :ttl"
            ),
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
                ":today": target_str,
                ":pid": recipient_person_id,
                ":ttl": ttl,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        logger.exception("DynamoDB update_item failed: %s", exc)
        return {"success": False, "message": "⚠️ Something went wrong. Try again later."}

    new_total = response["Attributes"].get("points", 1)
    date_note = f" _(backdated to {target_str})_" if override_date else ""
    return {
        "success": True,
        "message": (
            f"⭐ <@personId:{recipient_person_id}> just got a point{date_note}! "
            f"They now have **{new_total}** point{'s' if new_total != 1 else ''} this month."
        ),
    }


def remove_point(recipient_person_id: str, table_name: str, override_date: str = None) -> dict:
    """
    Remove 1 point from recipient. Admin-only.
    If override_date (YYYY-MM-DD) is given, removes from that month instead of current.
    Points floor at 0 — cannot go negative.
    """
    target_date = date.fromisoformat(override_date) if override_date else date.today()
    period = target_date.strftime("%Y-%m")
    period_label = target_date.strftime("%B %Y")
    table = _table(table_name)
    sk = f"user#{recipient_person_id}"

    try:
        existing = table.get_item(Key={"pk": period, "sk": sk}).get("Item")
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return {"success": False, "message": "⚠️ Something went wrong. Try again later."}

    current = int(existing.get("points", 0)) if existing else 0
    if current <= 0:
        return {
            "success": False,
            "message": f"<@personId:{recipient_person_id}> has no points to remove in {period_label}.",
        }

    try:
        response = table.update_item(
            Key={"pk": period, "sk": sk},
            UpdateExpression="SET points = points - :one",
            ConditionExpression="points > :zero",
            ExpressionAttributeValues={":one": 1, ":zero": 0},
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return {"success": False, "message": "⚠️ Points are already at 0."}
    except ClientError as exc:
        logger.exception("DynamoDB update_item failed: %s", exc)
        return {"success": False, "message": "⚠️ Something went wrong. Try again later."}

    new_total = int(response["Attributes"].get("points", 0))
    date_note = f" _(from {period_label})_" if override_date else ""
    return {
        "success": True,
        "message": (
            f"↩️ 1 point removed from <@personId:{recipient_person_id}>{date_note}. "
            f"They now have **{new_total}** point{'s' if new_total != 1 else ''} in {period_label}."
        ),
    }


def get_monthly_scores(period: str, table_name: str) -> list:
    """
    Return all scores for a given month period (e.g. '2026-04').
    Returns a list of dicts: [{ person_id, points }, ...]
    """
    table = _table(table_name)
    try:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(period)
        )
    except ClientError as exc:
        logger.exception("DynamoDB query failed: %s", exc)
        return []

    results = []
    for item in response.get("Items", []):
        if item["sk"].startswith("user#"):
            results.append({
                "person_id": item.get("person_id", item["sk"].replace("user#", "")),
                "points": int(item.get("points", 0)),
            })
    return results
