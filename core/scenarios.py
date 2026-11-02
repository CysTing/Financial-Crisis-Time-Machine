"""Named historical study windows. Labels disclose broad historical context."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    start: str
    end: str
    description: str
    crisis_start: str
    crisis_end: str


SCENARIOS = {
    "gfc": Scenario(
        "gfc", "2008 · Global Financial Crisis", "2007-01-01", "2010-12-31",
        "Begin before the banking crisis and make decisions through the collapse and recovery.",
        "2007-10-09", "2009-03-09",
    ),
    "covid": Scenario(
        "covid", "2020 · COVID Market Crash", "2019-01-01", "2021-12-31",
        "Navigate a global health shock, sudden market stress, and the policy response.",
        "2020-02-19", "2020-03-23",
    ),
    "inflation": Scenario(
        "inflation", "2022 · Inflation & Rate Shock", "2021-01-01", "2023-12-31",
        "Explore the pressure that inflation and rising rates placed on equities and bonds.",
        "2022-01-03", "2022-10-12",
    ),
}
