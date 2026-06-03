"""
One-time migration script — adds room_id to existing DynamoDB records.

All existing score records (sk = "user#<personId>") and raffle records
(sk = "YYYY-MM") pre-date multi-space support.  This script rewrites their
sort keys to the new format:
  - score records:  "user#<personId>"      → "<roomId>#user#<personId>"
  - raffle records: "YYYY-MM"              → "YYYY-MM#<roomId>"

and stamps each item with `room_id` and `space_name` attributes.

Usage
-----
# Dry run (no writes — prints what would change):
python scripts/migrate_room_ids.py --table pulse-bot-scores --token <WEBEX_TOKEN>

# Live run:
python scripts/migrate_room_ids.py --table pulse-bot-scores --token <WEBEX_TOKEN> --dry-run false
"""

from __future__ import annotations

import argparse
import io
import sys
from typing import Optional

# Force UTF-8 output on Windows so emoji in room names don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import boto3
import requests


# ─── helpers ─────────────────────────────────────────────────────────────────

def get_room_name(room_id: str, token: str) -> str:
    """Resolve a Webex room title, fallback to room_id on failure."""
    try:
        resp = requests.get(
            f"https://webexapis.com/v1/rooms/{room_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("title", room_id)
    except Exception as exc:
        print(f"[WARN] Could not resolve room name for {room_id}: {exc}")
    return room_id


def scan_all(table, **kwargs) -> list[dict]:
    """Full table scan, handling pagination automatically."""
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


# ─── migration ───────────────────────────────────────────────────────────────

def migrate(table_name: str, token: str, target_room_id: str, region: str, dry_run: bool) -> None:
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    target_space_name = get_room_name(target_room_id, token)
    print(f"\nTarget room : {target_room_id}")
    print(f"Room name   : {target_space_name}")
    print(f"Dry run     : {dry_run}\n")

    items = scan_all(table)
    print(f"Total items in table: {len(items)}")

    score_migrated = score_skipped = score_errors = 0
    raffle_migrated = raffle_skipped = raffle_errors = 0

    for item in items:
        pk = item.get("pk", "")
        sk = item.get("sk", "")

        # ── Old score record: sk starts with "user#" ─────────────────────────
        if sk.startswith("user#"):
            new_sk = f"{target_room_id}#{sk}"

            # Already migrated check
            if "#user#" in sk:
                print(f"  [SKIP] {pk}/{sk} already has new sk format")
                score_skipped += 1
                continue

            new_item = {
                **item,
                "sk": new_sk,
                "room_id": target_room_id,
                "space_name": target_space_name,
            }

            print(f"  [SCORE] {pk}/{sk} → {new_sk}")

            if not dry_run:
                try:
                    table.put_item(Item=new_item)
                    table.delete_item(Key={"pk": pk, "sk": sk})
                    score_migrated += 1
                except Exception as exc:
                    print(f"    [ERROR] {pk}/{sk}: {exc}")
                    score_errors += 1
            else:
                score_migrated += 1  # count as "would migrate" in dry run

        # ── Old raffle record: sk matches YYYY-MM (no '#' inside) ────────────
        elif item.get("pk", "").startswith("period#") and "#" not in sk:
            new_sk = f"{sk}#{target_room_id}"

            new_item = {
                **item,
                "sk": new_sk,
                "room_id": target_room_id,
                "space_name": target_space_name,
            }

            print(f"  [RAFFLE] {pk}/{sk} → {new_sk}")

            if not dry_run:
                try:
                    table.put_item(Item=new_item)
                    table.delete_item(Key={"pk": pk, "sk": sk})
                    raffle_migrated += 1
                except Exception as exc:
                    print(f"    [ERROR] {pk}/{sk}: {exc}")
                    raffle_errors += 1
            else:
                raffle_migrated += 1

        else:
            # New-format record or unknown — skip
            pass

    print("\n── Migration Summary ─────────────────────────────────────")
    print(f"Score records  : {score_migrated} {'would be ' if dry_run else ''}migrated, {score_skipped} skipped, {score_errors} errors")
    print(f"Raffle records : {raffle_migrated} {'would be ' if dry_run else ''}migrated, 0 skipped, {raffle_errors} errors")
    if dry_run:
        print("\n[DRY RUN — no changes written. Pass --dry-run false to apply.]\n")
    else:
        print("\n[Migration complete.]\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Migrate DynamoDB records to per-space sk format")
    parser.add_argument("--table", required=True, help="DynamoDB table name (e.g. pulse-bot-scores)")
    parser.add_argument("--token", required=True, help="Webex bot token (to resolve room name)")
    parser.add_argument(
        "--room-id",
        default="Y2lzY29zcGFyazovL3VzL1JPT00vYzJhODliMTAtYjU0My0xMWVlLTk3ZDctMDUzOWRiZmE4OGYw",
        help="Target room ID to stamp on all existing records (default: Space #2)",
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--dry-run",
        default="true",
        choices=["true", "false"],
        help="Set to 'false' to apply changes (default: true = dry run)",
    )

    args = parser.parse_args()
    dry_run = args.dry_run.lower() != "false"

    migrate(
        table_name=args.table,
        token=args.token,
        target_room_id=args.room_id,
        region=args.region,
        dry_run=dry_run,
    )


if __name__ == "__main__":
    main()
