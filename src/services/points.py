"""
Points service — reads and writes scores to DynamoDB.

Table schema (score records):
  pk         = "YYYY-MM"           (partition key — the month)
  sk         = "user#<personId>"   (sort key)
  points     = int                 (total points this month)
  dates      = StringSet           (every date a point was received e.g. {"2026-04-01","2026-04-03"})
  last_given = "YYYY-MM-DD"        (most recent date — used for no-date removes)
  person_id  = str
  ttl        = int                 (epoch — auto-delete after 13 months)

Raffle winner records:
  pk         = "raffle"
  sk         = "YYYY-MM"           (the month the raffle was run)
  person_id  = str                 (winner person ID)
"""

import logging
import os
import time
from datetime import date

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from services.utils import today_et

logger = logging.getLogger(__name__)


def _table(table_name: str):
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return dynamodb.Table(table_name)


def _ttl() -> int:
    """13-month TTL in epoch seconds."""
    return int(time.time()) + (13 * 30 * 24 * 60 * 60)


def award_point(
    giver_person_id: str,
    recipient_person_id: str,
    room_id: str,
    table_name: str,
    override_date: str = None,
) -> dict:
    """
    Give recipient_person_id 1 point.
    Rules:
      - A person cannot give points to themselves.
      - Only 1 point per recipient per day (checked against the dates set).
      - Admins can backdate via override_date; rejected if that date already exists.
    """
    target_date = date.fromisoformat(override_date) if override_date else today_et()
    period = target_date.strftime("%Y-%m")
    target_str = target_date.isoformat()

    if giver_person_id == recipient_person_id:
        return {"success": False, "message": "You can not give yourself a point, cheeky!"}

    table = _table(table_name)
    sk = f"user#{recipient_person_id}"

    try:
        existing = table.get_item(Key={"pk": period, "sk": sk}).get("Item")
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return {"success": False, "message": "Something went wrong. Try again later."}

    existing_dates = existing.get("dates", set()) if existing else set()
    if target_str in existing_dates:
        current = int(existing.get("points", 0))
        date_label = "today" if not override_date else target_str
        return {
            "success": False,
            "message": (
                f"<@personId:{recipient_person_id}> already received a point on {date_label}! "
                f"They have **{current}** point{'s' if current != 1 else ''} this month."
            ),
        }

    try:
        response = table.update_item(
            Key={"pk": period, "sk": sk},
            UpdateExpression=(
                "SET points = if_not_exists(points, :zero) + :one, "
                "last_given = :today, "
                "person_id = :pid, "
                "#ttl = :ttl "
                "ADD #dates :date_set"
            ),
            ExpressionAttributeNames={"#ttl": "ttl", "#dates": "dates"},
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
                ":today": target_str,
                ":pid": recipient_person_id,
                ":ttl": _ttl(),
                ":date_set": {target_str},
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        logger.exception("DynamoDB update_item failed: %s", exc)
        return {"success": False, "message": "Something went wrong. Try again later."}

    new_total = int(response["Attributes"].get("points", 1))
    date_note = f" _(backdated to {target_str})_" if override_date else ""
    return {
        "success": True,
        "message": (
            f"<@personId:{recipient_person_id}> just got a point{date_note}! "
            f"They now have **{new_total}** point{'s' if new_total != 1 else ''} this month."
        ),
    }


def remove_point(recipient_person_id: str, table_name: str, override_date: str = None) -> dict:
    """
    Remove 1 point from recipient. Admin-only.
    - With override_date: removes that specific date from the dates set.
      Rejected if that date was never awarded.
    - Without override_date: removes the most recently awarded date (last_given).
    """
    table = _table(table_name)
    sk = f"user#{recipient_person_id}"

    if override_date:
        target_date = date.fromisoformat(override_date)
        period = target_date.strftime("%Y-%m")
        period_label = target_date.strftime("%B %Y")
        target_str = override_date
    else:
        period = today_et().strftime("%Y-%m")
        period_label = today_et().strftime("%B %Y")
        target_str = None

    try:
        existing = table.get_item(Key={"pk": period, "sk": sk}).get("Item")
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return {"success": False, "message": "Something went wrong. Try again later."}

    if not existing or int(existing.get("points", 0)) <= 0:
        return {
            "success": False,
            "message": f"<@personId:{recipient_person_id}> has no points to remove in {period_label}.",
        }

    existing_dates = existing.get("dates", set())

    if not target_str:
        target_str = existing.get("last_given")
        if not target_str:
            return {"success": False, "message": "Could not determine last awarded date."}

    if target_str not in existing_dates:
        return {
            "success": False,
            "message": f"<@personId:{recipient_person_id}> has no point recorded on **{target_str}**.",
        }

    remaining_dates = existing_dates - {target_str}
    new_last_given = max(remaining_dates) if remaining_dates else None

    update_expr = (
        "SET points = points - :one "
        + (", last_given = :new_last " if new_last_given else "")
        + "DELETE #dates :date_set"
    )
    expr_values = {":one": 1, ":zero": 0, ":date_set": {target_str}}
    if new_last_given:
        expr_values[":new_last"] = new_last_given

    try:
        response = table.update_item(
            Key={"pk": period, "sk": sk},
            UpdateExpression=update_expr,
            ConditionExpression="points > :zero",
            ExpressionAttributeNames={"#dates": "dates"},
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return {"success": False, "message": "Points are already at 0."}
    except ClientError as exc:
        logger.exception("DynamoDB update_item failed: %s", exc)
        return {"success": False, "message": "Something went wrong. Try again later."}

    new_total = int(response["Attributes"].get("points", 0))
    return {
        "success": True,
        "message": (
            f"1 point removed from <@personId:{recipient_person_id}> _(removed point from {target_str})_. "
            f"They now have **{new_total}** point{'s' if new_total != 1 else ''} in {period_label}."
        ),
    }


def get_person_score(person_id: str, table_name: str) -> int:
    """Return the current month's point total for a single person. Returns 0 if not found."""
    period = today_et().strftime("%Y-%m")
        return int(item.get("points", 0)) if item else 0
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return 0


def get_monthly_scores(period: str, table_name: str) -> list:
    """Return all scores for a given month period. Returns [{ person_id, points }, ...]"""
    table = _table(table_name)
    try:
        response = table.query(KeyConditionExpression=Key("pk").eq(period))
    except ClientError as exc:
        logger.exception("DynamoDB query failed: %s", exc)
        return []

    return [
        {
            "person_id": item.get("person_id", item["sk"].replace("user#", "")),
            "points": int(item.get("points", 0)),
        }
        for item in response.get("Items", [])
        if item["sk"].startswith("user#")
    ]


def get_last_raffle_winner(table_name: str) -> str | None:
    """Return last month's raffle winner person_id, or None."""
    today = today_et()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    period = f"{year}-{month:02d}"
    table = _table(table_name)
    try:
        item = table.get_item(Key={"pk": "raffle", "sk": period}).get("Item")
        return item.get("person_id") if item else None
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return None


def save_raffle_winner(person_id: str, period: str, table_name: str) -> None:
    """Persist the raffle winner for a given month period."""
    table = _table(table_name)
    try:
        table.put_item(Item={"pk": "raffle", "sk": period, "person_id": person_id, "ttl": _ttl()})
    except ClientError as exc:
        logger.exception("Failed to save raffle winner: %s", exc)