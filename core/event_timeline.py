"""Verified historical announcements disclosed only at a causally eligible close."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from core.data_loader import _calendar, exchange_sessions


EVENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "events" / "events.json"


def disclosure_session(event: dict) -> pd.Timestamp:
    """Map publication to the first eligible XNYS close in New York time.

    Unknown publication times are conservatively delayed to the next session
    strictly after the verified publication date. Known times use actual session
    closes, including early-close days, rather than assuming a universal 16:00.
    """
    date = pd.Timestamp(event.get("publication_date", event["event_date"])).normalize()
    date = date.tz_localize(None) if date.tzinfo else date
    sessions = exchange_sessions(date, date + pd.Timedelta(days=20))
    published_at = event.get("published_at")
    if not published_at:
        return sessions[sessions > date][0]
    timestamp = pd.Timestamp(published_at)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise")
    timestamp = timestamp.tz_convert("UTC")
    cal = _calendar((date - pd.Timedelta(days=8)).date().isoformat(), (date + pd.Timedelta(days=28)).date().isoformat())
    for session in sessions:
        if cal.session_close(session) >= timestamp:
            return session
    raise ValueError("Could not find a disclosure close after the publication timestamp.")


@lru_cache(maxsize=1)
def _curated_events() -> tuple[dict, ...]:
    records = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    for record in records:
        if not record["source_url"].startswith("https://"):
            raise ValueError("Curated events need a verified HTTPS primary-source reference.")
        record["disclosure_date"] = disclosure_session(record).date().isoformat()
    return tuple(sorted(records, key=lambda row: (row["disclosure_date"], row["event_date"], row["headline"])))


def visible_events(
    current_date: str | pd.Timestamp,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    *,
    events: Iterable[dict] | None = None,
) -> list[dict]:
    """Return detached eligible rows only; never give the browser a future feed."""
    cutoff = pd.Timestamp(current_date).tz_localize(None).normalize()
    first = pd.Timestamp(start).tz_localize(None).normalize() if start is not None else None
    last = min(cutoff, pd.Timestamp(end).tz_localize(None).normalize()) if end is not None else cutoff
    records = _curated_events() if events is None else events
    revealed: list[dict] = []
    for event in records:
        disclosure = pd.Timestamp(event["disclosure_date"]) if "disclosure_date" in event else disclosure_session(event)
        event_date = pd.Timestamp(event["event_date"])
        if disclosure <= last and (first is None or event_date >= first):
            row = deepcopy(event)
            row["disclosure_date"] = disclosure.date().isoformat()
            revealed.append(row)
    return sorted(revealed, key=lambda row: (row["disclosure_date"], row["event_date"]))


def event_policy() -> str:
    """Human-readable policy shared by the interface and downloadable reports."""
    return "Events use verified primary sources. Known release timestamps are compared with actual XNYS closes in America/New_York; after-close/weekend announcements wait for the next session. Unknown times are conservatively revealed on the next session after the publication date. Headlines describe only facts known then, not subsequent outcomes."
