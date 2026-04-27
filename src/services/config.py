"""
Config service — loads secrets from AWS Parameter Store.
All sensitive values live here, never in the repo.

Parameters stored in AWS SSM (under /pulse-bot/):
  /pulse-bot/bot_token       — Webex bot bearer token
  /pulse-bot/bot_person_id   — Webex bot's own personId
  /pulse-bot/allowed_space_ids — comma-separated list of allowed Webex space IDs
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_cache: dict = {}   # module-level cache so we only call SSM once per Lambda warm start


def get_config() -> dict:
    """Return bot config loaded from AWS Parameter Store."""
    global _cache
    if _cache:
        return _cache

    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    params = [
        "/pulse-bot/bot_token",
        "/pulse-bot/bot_person_id",
        "/pulse-bot/allowed_space_ids",
    ]

    try:
        response = ssm.get_parameters(Names=params, WithDecryption=True)
    except ClientError as exc:
        logger.exception("Failed to load config from SSM: %s", exc)
        raise

    result = {}
    for param in response["Parameters"]:
        key = param["Name"].split("/")[-1]   # strip "/pulse-bot/" prefix
        result[key] = param["Value"]

    # Convert comma-separated space IDs into a list
    result["allowed_space_ids"] = [
        s.strip() for s in result.get("allowed_space_ids", "").split(",") if s.strip()
    ]

    _cache = result
    logger.info("Config loaded from SSM. Allowed spaces: %d", len(result["allowed_space_ids"]))
    return _cache
