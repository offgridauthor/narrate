"""Waiting out rate limits and daily quotas."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from narrate.common import emit, log
from narrate.engines.base import RateLimited


# ---------------------------------------------------------------- waiting

def next_quota_reset() -> datetime:
    """When a daily quota resets. Gemini's free tier resets at midnight
    Pacific time, so that is the default when an engine doesn't say."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = timezone(timedelta(hours=-8))
    now = datetime.now(tz)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)
    return nxt


def wait_out(e: RateLimited, streak: int, no_wait: bool) -> None:
    if e.daily or streak >= 12:
        until = next_quota_reset()
        local = until.astimezone().strftime("%H:%M")
        if no_wait:
            emit("paused", reason="daily quota", until=until.isoformat(timespec="minutes"), local=local)
            raise SystemExit(75)
        emit("wait", reason="daily quota", until=until.isoformat(timespec="minutes"), local=local)
        log(f"daily quota reached; resuming at {until:%Y-%m-%d %H:%M %Z}")
        time.sleep(max(60, (until - datetime.now(until.tzinfo)).total_seconds()))
        return
    delay = min(900, e.delay or 10 * 2 ** min(streak, 6))
    emit("wait", reason="rate limit", seconds=round(delay))
    time.sleep(delay)
