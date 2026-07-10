from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

import redbeat
import redbeat.schedulers as redbeat_schedulers
from celery.schedules import crontab


SCHEDULE_TIMEZONE_HEADER = "gobii_schedule_timezone"


class NamedTimezoneCrontab(crontab):
    def __init__(
        self,
        minute="*",
        hour="*",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone_name="UTC",
        **kwargs,
    ):
        self.timezone_name = timezone_name
        self._named_timezone = ZoneInfo(timezone_name)
        super().__init__(
            minute=minute,
            hour=hour,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            **kwargs,
        )

    @property
    def tz(self):
        return self._named_timezone

    def now(self) -> datetime:
        return self.maybe_make_aware(super().now()).astimezone(self.tz)

    def remaining_delta(self, last_run_at, tz=None, ffwd=None):
        if last_run_at.tzinfo is not None:
            last_run_at = last_run_at.astimezone(self.tz)
        kwargs = {"tz": tz}
        if ffwd is not None:
            kwargs["ffwd"] = ffwd
        return super().remaining_delta(last_run_at, **kwargs)

    def __eq__(self, other):
        return (
            isinstance(other, NamedTimezoneCrontab)
            and self.timezone_name == other.timezone_name
            and super().__eq__(other)
        )

    def __reduce__(self):
        return (
            self.__class__,
            (
                self._orig_minute,
                self._orig_hour,
                self._orig_day_of_week,
                self._orig_day_of_month,
                self._orig_month_of_year,
                self.timezone_name,
            ),
            self._orig_kwargs,
        )


class GobiiRedBeatSchedulerEntry(redbeat_schedulers.RedBeatSchedulerEntry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        headers = (self.options or {}).get("headers") or {}
        timezone_name = headers.get(SCHEDULE_TIMEZONE_HEADER)
        if timezone_name and isinstance(self.schedule, crontab) and not isinstance(
            self.schedule,
            NamedTimezoneCrontab,
        ):
            schedule = self.schedule
            self.schedule = NamedTimezoneCrontab(
                minute=schedule._orig_minute,
                hour=schedule._orig_hour,
                day_of_week=schedule._orig_day_of_week,
                day_of_month=schedule._orig_day_of_month,
                month_of_year=schedule._orig_month_of_year,
                timezone_name=timezone_name,
                app=self.app,
            )

    @property
    def due_at(self):
        if not isinstance(self.schedule, NamedTimezoneCrontab):
            return super().due_at
        if self.last_run_at is None:
            return self._default_now()

        start, delta, now = self.schedule.remaining_delta(self.last_run_at)
        next_run = start + delta
        if next_run.astimezone(dt_timezone.utc) < now.astimezone(dt_timezone.utc):
            return self._default_now()
        return next_run


def redbeat_options_for_schedule(schedule) -> dict:
    timezone_name = getattr(schedule, "timezone_name", "")
    if not timezone_name:
        return {}
    return {"headers": {SCHEDULE_TIMEZONE_HEADER: timezone_name}}


def install_redbeat_timezone_serialization() -> None:
    redbeat_schedulers.RedBeatSchedulerEntry = GobiiRedBeatSchedulerEntry
    redbeat_schedulers.RedBeatScheduler.Entry = GobiiRedBeatSchedulerEntry
    redbeat.RedBeatSchedulerEntry = GobiiRedBeatSchedulerEntry
    redbeat.RedBeatScheduler.Entry = GobiiRedBeatSchedulerEntry
