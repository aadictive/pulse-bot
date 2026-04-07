# Pulse-Bot 🤖

Pulse-Bot is a Webex bot that tracks NYT Games and maintains a leaderboard.

## Features

- ✨ Tracks Wordle, Connections, Spelling Bee, Mini Crossword, Quordle, etc
- 🏆 Maintains a real-time leaderboard
- 📊 One point per person per day (max)
- 🎉 Monthly winner selection
- 🔐 Secure webhook validation
- 📝 Audit logging for all activity

## Setup

See SETUP.md for detailed instructions

## Architecture (AWS Hosted)

- **Frontend**: Webex Space
- **Webhook**: API Gateway + Lambda
- **Database**: DynamoDB
- **Secrets**: Parameter Store
- **Monitoring**: CloudWatch

## Security

- Uses AWS Parameter Store for secrets
- Webhook signature validation
- IAM least privilege access
- DynamoDB encryption at rest
- CloudWatch audit logging

## Cost (Approx.)

- Lambda: ~$0.20/month (1M free requests)
- DynamoDB: ~$1/month (25 units free)
- Parameter Store: ~FREE (10 free parameters)
- API Gateway: ~FREE (1M free requests)
- **Total: ~$1-2/month**
