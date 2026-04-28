# Pulse Bot 🤖

A self-hosted, low-cost Webex bot that gives people points, tracks monthly scores in DynamoDB, and automatically posts a leaderboard + raffle winner on the 1st of every month.

> Inspired by [points](https://github.com/webex/pointspublic) but fully self-hosted on AWS with no third-party dependencies.

---

## Features

- ⭐ Give points by mentioning someone — `@Pulse @Name`
- 📊 View scores for yourself, someone else, or the full leaderboard
- 🔒 1 point per person per day — enforced via a date set in DynamoDB
- 🚫 No self-points
- 🏆 Automatic monthly leaderboard posted on the 1st of every month
- 🎰 Weighted raffle winner (more points = more tickets, last month's winner excluded)
- 🔐 Only messages from approved Webex spaces are processed
- 🛡️ Admin-only commands for corrections (remove points, backdate awards)
- 💾 All secrets stored in AWS Parameter Store — nothing sensitive in the repo

---

## Architecture

```
Webex Space
    │
    ▼
API Gateway  (POST /webhook)
    │
    ▼
Lambda: webhook.py          ←── AWS Parameter Store (secrets)
    │                                   │
    ▼                                   │
DynamoDB                    ←───────────┘
    ▲
    │
Lambda: leaderboard.py  ←── EventBridge (cron: 1st of every month, 9am UTC)
```

**AWS Services used:**
| Service | Purpose | Approx. Cost |
|---|---|---|
| Lambda | Bot logic | ~$0.00/month (free tier) |
| API Gateway | Webhook endpoint | ~$0.00/month (free tier) |
| DynamoDB | Score storage | ~$0.00/month (on-demand, pay per use) |
| Parameter Store | Secrets (bot token, space IDs, admins) | Free |
| EventBridge | Monthly leaderboard schedule | Free |
| CloudWatch | Logs | ~$0.00/month (free tier) |
| S3 | SAM deployment artifacts | ~$0.01/month |
| **Total** | | **< $1/month** |

---

## Bot Commands

### Everyone
| Command | Description |
|---|---|
| `@Pulse @Name` | Give someone 1 point (1 per person per day) |
| `@Pulse score me` | See your own score this month |
| `@Pulse score @Name` | See someone else's score |
| `@Pulse scores` | See the full leaderboard |
| `@Pulse help` | Show all available commands |

### Admins only
| Command | Description |
|---|---|
| `@Pulse @Name --` | Remove the most recently awarded point |
| `@Pulse @Name -- 2026-03-15` | Remove the point awarded on a specific date |
| `@Pulse @Name 2026-04-26` | Backdate a point to a specific date |

> Admin commands tag all admins in the error message if a non-admin attempts them.

---

## Prerequisites

- AWS account with IAM user that has permissions for: Lambda, DynamoDB, API Gateway, SSM, IAM, CloudFormation, S3, EventBridge
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) installed
- [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) installed
- Python 3.9+
- A Webex Bot token from [developer.webex.com](https://developer.webex.com)

---

## Setup Guide

### Step 1 — Configure AWS CLI

```bash
aws configure
# AWS Access Key ID: <your key>
# AWS Secret Access Key: <your secret>
# Default region name: us-east-1
# Default output format: json
```

### Step 2 — Get your Webex Bot details

**Bot token** — from [developer.webex.com](https://developer.webex.com) → My Apps → your bot → copy the token.

**Bot person ID** — run this with your bot token:
```bash
curl -H "Authorization: Bearer YOUR_BOT_TOKEN" https://webexapis.com/v1/people/me
# Copy the "id" field
```

**Webex Space ID** — add the bot to your space, then run:
```bash
curl -H "Authorization: Bearer YOUR_BOT_TOKEN" https://webexapis.com/v1/rooms
# Find your space and copy its "id"
```

**Your person ID (for admin access)** — run with your own token or the bot token + your email:
```bash
curl -H "Authorization: Bearer YOUR_BOT_TOKEN" "https://webexapis.com/v1/people?email=you@company.com"
# Copy the "id" field from items[0]
```

### Step 3 — Store secrets in AWS Parameter Store

```bash
# Bot bearer token
aws ssm put-parameter \
  --name "/pulse-bot/bot_token" \
  --value "YOUR_BOT_TOKEN" \
  --type "SecureString"

# Bot's own Webex person ID (so it ignores its own messages)
aws ssm put-parameter \
  --name "/pulse-bot/bot_person_id" \
  --value "YOUR_BOT_PERSON_ID" \
  --type "SecureString"

# Comma-separated list of allowed Webex space IDs
aws ssm put-parameter \
  --name "/pulse-bot/allowed_space_ids" \
  --value "SPACE_ID_1,SPACE_ID_2" \
  --type "SecureString"

# Comma-separated list of admin Webex person IDs
aws ssm put-parameter \
  --name "/pulse-bot/admin_person_ids" \
  --value "YOUR_PERSON_ID" \
  --type "SecureString"
```

### Step 4 — Create the S3 bucket for SAM artifacts

```bash
# Replace 123456789012 with your AWS account ID
aws s3 mb s3://pulse-bot-sam-artifacts-123456789012 --region us-east-1
```

### Step 5 — Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | Your AWS access key ID |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret access key |
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID |

### Step 6 — Deploy

Push to the `develop` branch (via a PR) and GitHub Actions will automatically:
1. Build the Lambda package
2. Deploy via SAM (creates all AWS resources)
3. Print the webhook URL

Or deploy manually:
```bash
sam build
sam deploy
```

### Step 7 — Register the Webex Webhook

After deploy, copy the webhook URL from the GitHub Actions output (or CloudFormation outputs) and register it with Webex:

```bash
curl -X POST https://webexapis.com/v1/webhooks \
  -H "Authorization: Bearer YOUR_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Pulse Bot Webhook",
    "targetUrl": "https://YOUR_API_ID.execute-api.us-east-1.amazonaws.com/dev/webhook",
    "resource": "messages",
    "event": "created"
  }'
```

### Step 8 — Test

In your Webex space:
```
@Pulse help
@Pulse @SomeColleague
@Pulse scores
```

---

## Manual Leaderboard (Testing or Past Months)

You can manually trigger the leaderboard Lambda for any month:

```bash
# Write payload to file (avoids shell quoting issues)
echo '{"period": "2026-04"}' > payload.json

aws lambda invoke \
  --function-name pulse-bot-leaderboard-dev \
  --payload file://payload.json \
  --cli-binary-format raw-in-base64-out \
  response.json

cat response.json
```

Omit `period` to default to last month (same as the automatic run).

---

## DynamoDB Schema

**Score records:**
```
pk         = "YYYY-MM"          partition key (month)
sk         = "user#<personId>"  sort key
points     = Number             total points this month
dates      = StringSet          every date a point was received {"2026-04-01", ...}
last_given = "YYYY-MM-DD"       most recently awarded date
person_id  = String             Webex person ID
ttl        = Number             auto-deleted after 13 months
```

**Raffle winner records:**
```
pk         = "raffle"
sk         = "YYYY-MM"          the month the raffle ran
person_id  = String             winner's Webex person ID
ttl        = Number             auto-deleted after 13 months
```

---

## CI/CD

Every push to `develop` (via PR) triggers GitHub Actions which:
1. Installs SAM CLI
2. Runs `sam build`
3. Runs `sam deploy`
4. Prints the live webhook URL

---

## Security

- All secrets stored in AWS Parameter Store (SecureString / KMS encrypted) — never in the repo
- Only messages from explicitly configured Webex space IDs are processed
- Lambda IAM role has least-privilege access (DynamoDB + SSM only)
- SSM config is cached with a 5-minute TTL so admin list changes take effect quickly
- DynamoDB encryption at rest enabled by default

