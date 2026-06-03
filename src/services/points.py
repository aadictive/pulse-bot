"""
Points service — reads and writes scores to DynamoDB.

Table schema (score records):
  pk         = "YYYY-MM"                       (partition key — the month)
  sk         = "<roomId>#user#<personId>"       (sort key — space-scoped per user)
  points     = int                              (total points this month)
  dates      = StringSet                        (every date a point was received)
  last_given = "YYYY-MM-DD"                    (most recent date — used for no-date removes)
  person_id  = str                              (Webex person ID)
  room_id    = str                              (Webex room/space ID)
  user_name  = str  (optional)                 (display name — informational, set by award_point)
  space_name = str  (optional)                 (space title — informational, set by award_point)
  ttl        = int                              (epoch — auto-delete after 13 months)

Raffle winner records:
  pk         = "raffle"
  sk         = "YYYY-MM#<roomId>"              (month + space — one winner per space per month)
  person_id  = str                              (winner person ID)
  room_id    = str
  user_name  = str  (optional)
  space_name = str  (optional)
"""

import logging
import os
import time
from datetime import date
from typing import Optional

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
    recipient_name: str = None,
    space_name: str = None,
) -> dict:
    """
    Give recipient_person_id 1 point in the given room (space).
    Rules:
      - A person cannot give points to themselves.
      - Only 1 point per recipient per day per space (checked against the dates set).
      - Admins can backdate via override_date; rejected if that date already exists.

    recipient_name and space_name are informational only — stored in DynamoDB so
    records are human-readable without cross-referencing IDs externally.
    All business logic (deduplication, limits, leaderboard) keys off the UID and room_id.
    """
    target_date = date.fromisoformat(override_date) if override_date else today_et()
    period = target_date.strftime("%Y-%m")
    target_str = target_date.isoformat()

    if giver_person_id == recipient_person_id:
        return {"success": False, "message": "You can not give yourself a point, cheeky!"}

    table = _table(table_name)
    sk = f"{room_id}#user#{recipient_person_id}"

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
        set_clause = (
            "SET points = if_not_exists(points, :zero) + :one, "
            "last_given = :today, "
            "person_id = :pid, "
            "room_id = :rid, "
            "#ttl = :ttl"
            + (", user_name = :uname" if recipient_name else "")
            + (", space_name = :sname" if space_name else "")
            + " ADD #dates :date_set"
        )
        expr_values = {
            ":zero": 0,
            ":one": 1,
            ":today": target_str,
            ":pid": recipient_person_id,
            ":rid": room_id,
            ":ttl": _ttl(),
            ":date_set": {target_str},
        }
        if recipient_name:
            expr_values[":uname"] = recipient_name
        if space_name:
            expr_values[":sname"] = space_name

        response = table.update_item(
            Key={"pk": period, "sk": sk},
            UpdateExpression=set_clause,
            ExpressionAttributeNames={"#ttl": "ttl", "#dates": "dates"},
            ExpressionAttributeValues=expr_values,
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


def remove_point(recipient_person_id: str, room_id: str, table_name: str, override_date: str = None) -> dict:
    """
    Remove 1 point from recipient in the given space. Admin-only.
    - With override_date: removes that specific date from the dates set.
      Rejected if that date was never awarded.
    - Without override_date: removes the most recently awarded date (last_given).
    """
    table = _table(table_name)
    sk = f"{room_id}#user#{recipient_person_id}"

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


def get_person_score(person_id: str, room_id: str, table_name: str) -> int:
    """Return the current month's point total for a single person in a given space. Returns 0 if not found."""
    period = today_et().strftime("%Y-%m")
    table = _table(table_name)
    sk = f"{room_id}#user#{person_id}"
    try:
        item = table.get_item(Key={"pk": period, "sk": sk}).get("Item")
        return int(item.get("points", 0)) if item else 0
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return 0


def get_monthly_scores(period: str, room_id: str, table_name: str) -> list:
    """Return all scores for a given month and space. Returns [{ person_id, points, user_name, space_name }, ...]"""
    table = _table(table_name)
    try:
        response = table.query(
            KeyConditionExpression=Key("pk").eq(period) & Key("sk").begins_with(f"{room_id}#user#")
        )
    except ClientError as exc:
        logger.exception("DynamoDB query failed: %s", exc)
        return []

    return [
        {
            "person_id": item.get("person_id", item["sk"].split("#user#")[-1]),
            "points": int(item.get("points", 0)),
            "user_name": item.get("user_name"),
            "space_name": item.get("space_name"),
        }
        for item in response.get("Items", [])
        if "#user#" in item["sk"]
    ]


def get_last_raffle_winner(table_name: str, room_id: str) -> Optional[str]:
    """Return last month's raffle winner person_id for a given space, or None."""
    today = today_et()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    period = f"{year}-{month:02d}"
    sk = f"{period}#{room_id}"
    table = _table(table_name)
    try:
        item = table.get_item(Key={"pk": "raffle", "sk": sk}).get("Item")
        return item.get("person_id") if item else None
    except ClientError as exc:
        logger.exception("DynamoDB get_item failed: %s", exc)
        return None


def save_raffle_winner(person_id: str, period: str, room_id: str, table_name: str, winner_name: str = None, space_name: str = None) -> None:
    """Persist the raffle winner for a given month and space."""
    table = _table(table_name)
    item = {
        "pk": "raffle",
        "sk": f"{period}#{room_id}",
        "person_id": person_id,
        "room_id": room_id,
        "ttl": _ttl(),
    }
    if winner_name:
        item["user_name"] = winner_name
    if space_name:
        item["space_name"] = space_name
    try:
        table.put_item(Item=item)
    except ClientError as exc:
        logger.exception("Failed to save raffle winner: %s", exc)