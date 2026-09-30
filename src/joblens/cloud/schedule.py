"""When the nightly job fetches by itself: the owner's schedule (7.10.3).

Mahdi's choice (2026-09-30): the schedule is kept in JobLens (app_settings),
not in Cloud Scheduler. Cloud Scheduler only wakes the job every hour, and the
job asks this whether the hour is one of the owner's; if not, it leaves within
seconds without asking any site. So the page never needs a right to change
Cloud Scheduler, which cannot be granted for one scheduler job alone.

The form is weekdays and one or two hours a day, in Dutch time -- no cron
text. Two hours must be at least MIN_GAP apart: every run asks every site
again, and two runs close together would find almost nothing new.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TZ = ZoneInfo("Europe/Amsterdam")
MIN_GAP = 6  # hours between the two runs of a day, counted round the clock
DAYS = range(7)  # Monday is 0, as datetime.weekday() counts


class Schedule(BaseModel):
    """Which weekdays, and at which hours of them, the job fetches."""

    model_config = ConfigDict(extra="forbid")

    days: list[int] = Field(
        default_factory=lambda: list(DAYS), min_length=1, max_length=7
    )
    hours: list[int] = Field(default_factory=lambda: [3], min_length=1, max_length=2)

    @field_validator("days", "hours")
    @classmethod
    def sorted_once(cls, values: list[int]) -> list[int]:
        return sorted(set(values))

    @model_validator(mode="after")
    def in_range(self) -> "Schedule":
        if any(day not in DAYS for day in self.days):
            raise ValueError("a day is 0 (Monday) to 6 (Sunday)")
        if any(not 0 <= hour <= 23 for hour in self.hours):
            raise ValueError("an hour is 0 to 23")
        if len(self.hours) == 2:
            first, second = self.hours
            gap = min(second - first, 24 - (second - first))
            if gap < MIN_GAP:
                raise ValueError(
                    f"two runs a day must be at least {MIN_GAP} hours apart"
                )
        return self

    def due(self, now: datetime) -> bool:
        """Whether a run belongs in the hour `now` falls in, Dutch time."""
        local = now.astimezone(TZ)
        return local.weekday() in self.days and local.hour in self.hours

    def next_after(self, now: datetime) -> datetime:
        """The start of the next scheduled hour after `now`, in Dutch time.
        Walks hour by hour, which is also right across a change of clocks."""
        # Stepped in UTC: adding hours to a Dutch time moves its wall clock,
        # and would count 02:00 twice (or never) on the night clocks change.
        hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        for step in range(1, 8 * 24 + 2):
            candidate = (hour + timedelta(hours=step)).astimezone(TZ)
            if candidate.weekday() in self.days and candidate.hour in self.hours:
                return candidate
        raise AssertionError("a schedule always has a day and an hour")
