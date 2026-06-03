#!/usr/bin/env python3
"""
backfill_usernames.py — One-time script to populate user_name on existing DynamoDB records.

Point records created before feature/22-store-display-names was deployed only contain
an opaque Webex person ID. This script scans the table, resolves each missing display
name via the Webex API, and writes it back in-place.

Usage:
    python scripts/backfill_usernames.py \
        --table pulse-bot-scores-dev \
        --token YOUR_BOT_TOKEN \
        [--region us-east-1] \
        [--delay 0.5] \
        [--dry-run]

Arguments:
    --table    DynamoDB table name (required)
    --token    Webex bot bearer token (required)
    --region   AWS region (default: us-east-1)
    --delay    Seconds to wait between Webex API calls (default: 0.5)
    --dry-run  Scan and resolve names but do NOT write to DynamoDB

Prerequisites:
    pip install boto3 requests
    AWS credentials configured (aws configure) with DynamoDB read/write access.
"""

import argparse
import time

import boto3
import requests


WEBEX_API = "https://webexapis.com/v1"


def resolve_display_name(person_id: str, token: str) -> str | None:
    """Call GET /people/{personId} and return displayName, or None on failure."""
    try:
        resp = requests.get(
            f"{WEBEX_API}/people/{person_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("displayName") or data.get("firstName")
    except requests.RequestException as exc:
        print(f"  ⚠️  API error for {person_id}: {exc}")
        return None


def backfill(table_name: str, token: str, region: str, delay: float, dry_run: bool) -> None:
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    print(f"\n🔍 Scanning table: {table_name} (region: {region})")
    if dry_run:
        print("   DRY RUN — no writes will be made\n")

    updated = 0
    skipped = 0
    failed = 0
    total = 0

    # Paginate through all items — FilterExpression keeps only user# records
    scan_kwargs = {
        "FilterExpression": boto3.dynamodb.conditions.Attr("sk").begins_with("user#"),
    }

    while True:
        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])

        for item in items:
            total += 1
            person_id = item.get("person_id") or item["sk"].replace("user#", "")
            existing_name = item.get("user_name")

            if existing_name:
                print(f"  ⏭️  {person_id[:20]}... already has name '{existing_name}' — skipping")
                skipped += 1
                continue

            print(f"  🔎  Resolving {person_id[:20]}...", end=" ", flush=True)
            name = resolve_display_name(person_id, token)

            if not name:
                print("FAILED")
                failed += 1
                continue

            print(f"→ '{name}'", end="")

            if not dry_run:
                try:
                    table.update_item(
                        Key={"pk": item["pk"], "sk": item["sk"]},
                        UpdateExpression="SET user_name = :uname",
                        ExpressionAttributeValues={":uname": name},
                    )
                    print(" ✅")
                    updated += 1
                except Exception as exc:
                    print(f" ❌ DynamoDB error: {exc}")
                    failed += 1
            else:
                print(" (dry run)")
                updated += 1

            time.sleep(delay)

        # Handle DynamoDB pagination
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    print(f"\n{'─' * 50}")
    print(f"✅ Updated : {updated}")
    print(f"⏭️  Skipped : {skipped}")
    print(f"❌ Failed  : {failed}")
    print(f"📦 Total   : {total}")
    if dry_run:
        print("\n(Dry run complete — re-run without --dry-run to apply changes)")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill user_name into existing Pulse Bot DynamoDB records."
    )
    parser.add_argument("--table", required=True, help="DynamoDB table name")
    parser.add_argument("--token", required=True, help="Webex bot bearer token")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between Webex API calls for rate limiting (default: 0.5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve names but do not write to DynamoDB",
    )
    args = parser.parse_args()

    backfill(
        table_name=args.table,
        token=args.token,
        region=args.region,
        delay=args.delay,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
