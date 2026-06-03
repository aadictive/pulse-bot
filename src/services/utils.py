"""
Shared utilities for the Pulse Bot.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# All daily-limit and month-period logic uses Eastern Time so that late-evening
# awards (e.g. 9 PM ET) are attributed to the correct calendar day rather than
# rolling over to the next UTC day.
_ET = ZoneInfo("America/New_York")


def today_et() -> date:
    """Return today's date in US Eastern Time (ET/EDT)."""
    return datetime.now(tz=_ET).date()
