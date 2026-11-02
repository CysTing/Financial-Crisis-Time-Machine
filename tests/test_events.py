"""Conservative event disclosure and restricted presentation boundaries."""

import pandas as pd

from core.event_timeline import disclosure_session, visible_events


def event(date, published_at=None):
    row = {"event_date": date, "publication_date": date, "headline": "Verified test announcement", "explanation": "A deterministic event fixture.", "source_url": "https://example.test/primary"}
    if published_at:
        row["published_at"] = published_at
    return row


def test_unknown_time_waits_until_next_session():
    assert disclosure_session(event("2008-09-15")) == pd.Timestamp("2008-09-16")


def test_known_preclose_publication_disclosed_same_session():
    assert disclosure_session(event("2020-03-03", "2020-03-03T10:00:00-05:00")) == pd.Timestamp("2020-03-03")


def test_afterclose_announcement_waits_until_next_session():
    assert disclosure_session(event("2008-09-16", "2008-09-16T21:00:00-04:00")) == pd.Timestamp("2008-09-17")


def test_weekend_announcement_disclosed_monday():
    assert disclosure_session(event("2020-03-15", "2020-03-15T17:00:00-04:00")) == pd.Timestamp("2020-03-16")


def test_holiday_and_early_closes_use_exchange_schedule():
    # July 3 is a 13:00 New York close and July 4 is a holiday.
    assert disclosure_session(event("2023-07-03", "2023-07-03T14:00:00-04:00")) == pd.Timestamp("2023-07-05")
    assert disclosure_session(event("2023-07-04")) == pd.Timestamp("2023-07-05")


def test_future_event_not_present_in_returned_payload():
    events = [event("2008-09-15"), event("2008-09-16", "2008-09-16T21:00:00-04:00")]
    assert visible_events("2008-09-15", events=events) == []
    revealed = visible_events("2008-09-16", events=events)
    assert len(revealed) == 1
    assert revealed[0]["event_date"] == "2008-09-15"
    assert all(row["disclosure_date"] <= "2008-09-16" for row in revealed)


def test_revealed_events_are_detached_and_scenario_restricted():
    rows = [event("2008-09-15"), event("2020-03-03", "2020-03-03T10:00:00-05:00")]
    revealed = visible_events("2020-03-10", start="2019-01-01", events=rows)
    assert len(revealed) == 1
    revealed[0]["headline"] = "mutation"
    assert rows[1]["headline"] != "mutation"


def test_curated_lehman_and_aig_source_disclosure():
    september16 = visible_events("2008-09-16", start="2008-09-01")
    assert any("Lehman" in row["headline"] for row in september16)
    assert not any("AIG" in row["headline"] for row in september16)
    september17 = visible_events("2008-09-17", start="2008-09-01")
    assert any("AIG" in row["headline"] for row in september17)
    assert all(row["source_url"].startswith("https://") for row in september17)
