# scripts/

One-time maintenance and migration scripts. These are **not** deployed to Lambda — they run locally using your AWS credentials and Webex bot token.

---

## backfill_usernames.py

Populates the `user_name` attribute on existing DynamoDB score records that pre-date display name storage (Issue #22). Resolves each missing name via the Webex API and writes it back in-place.

```bash
# Dry run — shows what would change, writes nothing
python scripts/backfill_usernames.py --table pulse-bot-scores-dev --token <BOT_TOKEN>

# Apply
python scripts/backfill_usernames.py --table pulse-bot-scores-dev --token <BOT_TOKEN> --delay 0.5
```

---

## migrate_room_ids.py

Migrates existing DynamoDB records to the per-space sort key format introduced in Issue #20. Rewrites score and raffle record sort keys to include `room_id`, and stamps `room_id` + `space_name` on every item.

Targets Space #2 from `allowed_space_ids` by default (the original space where all points were awarded before multi-space support).

```bash
# Dry run — shows what would change, writes nothing
python scripts/migrate_room_ids.py --table pulse-bot-scores-dev --token <BOT_TOKEN>

# Apply
python scripts/migrate_room_ids.py --table pulse-bot-scores-dev --token <BOT_TOKEN> --dry-run false
```
