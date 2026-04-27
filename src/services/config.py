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
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_cache: dict = {}
_cache_loaded_at: float = 0
_CACHE_TTL_SECONDS = 300   # refresh SSM config every 5 minutes


def get_config() -> dict:
    """Return bot config loaded from AWS Parameter Store."""
    global _cache, _cache_loaded_at
    if _cache and (time.time() - _cache_loaded_at) < _CACHE_TTL_SECONDS:
        return _cache

    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    params = [
        "/pulse-bot/bot_token",
        "/pulse-bot/bot_person_id",
        "/pulse-bot/allowed_space_ids",
        "/pulse-bot/admin_person_ids",
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

    # Convert comma-separated admin person IDs into a list
    result["admin_person_ids"] = [
        s.strip() for s in result.get("admin_person_ids", "").split(",") if s.strip()
    ]

    _cache = result
    _cache_loaded_at = time.time()
    logger.info("Config loaded from SSM. Allowed spaces: %d", len(result["allowed_space_ids"]))
    return _cache
