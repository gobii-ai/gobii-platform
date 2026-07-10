from celery.schedules import crontab, schedule as celery_schedule
from datetime import timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from api.services.redbeat_timezone import NamedTimezoneCrontab


class ScheduleParser:
    """Parses a schedule string into a celery schedule object."""

    SHORTHANDS = {
        "@annually": "0 0 1 1 *",
        "@yearly": "0 0 1 1 *",
        "@monthly": "0 0 1 * *",
        "@weekly": "0 0 * * 0",
        "@daily": "0 0 * * *",
        "@hourly": "0 * * * *",
    }

    INTERVAL_REGEX = re.compile(r"(@every)\s+(.*)")
    TIMEZONE_REGEX = re.compile(r"^(?:CRON_TZ|TZ)=(?P<timezone>\S+)\s+(?P<schedule>.+)$")
    POSTFIX_TIMEZONE_REGEX = re.compile(r"^(?P<schedule>.+?)\s+(?:CRON_TZ|TZ)=(?P<timezone>\S+)$")
    UNIT_MAP = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}

    @classmethod
    def canonicalize(cls, schedule_str: str) -> str:
        """Return the canonical stored form, accepting a misplaced timezone suffix."""
        normalized = schedule_str.strip()
        postfix_match = cls.POSTFIX_TIMEZONE_REGEX.match(normalized)
        return (
            f"CRON_TZ={postfix_match.group('timezone')} {postfix_match.group('schedule').strip()}"
            if postfix_match else normalized
        )

    @classmethod
    def parse(cls, schedule_str: str) -> celery_schedule | None:
        """Parse a schedule string, returning None when disabled and raising ValueError when invalid."""
        if not schedule_str:
            return None

        schedule_str = cls.canonicalize(schedule_str)

        timezone_name = None
        timezone_match = cls.TIMEZONE_REGEX.match(schedule_str)
        if timezone_match:
            timezone_name = timezone_match.group("timezone")
            schedule_str = timezone_match.group("schedule").strip()
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"Unknown schedule timezone: {timezone_name}") from exc

        if schedule_str in cls.SHORTHANDS:
            schedule_str = cls.SHORTHANDS[schedule_str]

        if schedule_str == "@reboot":
            raise ValueError("Unsupported schedule format: @reboot")

        interval_match = cls.INTERVAL_REGEX.match(schedule_str)
        if interval_match:
            if timezone_name:
                raise ValueError("CRON_TZ applies only to cron or cron shorthand schedules.")
            interval_str = interval_match.group(2).strip()
            return cls._parse_interval(interval_str)

        return cls._parse_crontab(schedule_str, timezone_name=timezone_name)

    @classmethod
    def _parse_interval(cls, interval_str: str) -> celery_schedule:
        """Parses an interval string like '30m' or '2h 30m'."""
        total_seconds = 0
        parts = interval_str.split()
        for part in parts:
            value_str = part[:-1]
            unit = part[-1]

            if not value_str.isdigit() or unit not in cls.UNIT_MAP:
                raise ValueError(f"Invalid interval part: {part}")
            
            value = int(value_str)
            total_seconds += timedelta(**{cls.UNIT_MAP[unit]: value}).total_seconds()
        
        if total_seconds <= 0:
            raise ValueError("Interval must be positive.")

        return celery_schedule(run_every=total_seconds)

    @classmethod
    def _parse_crontab(cls, schedule_str: str, *, timezone_name: str | None = None):
        """Parses a crontab string."""
        parts = schedule_str.split()
        if len(parts) != 5:
            raise ValueError("Invalid cron format. Expected: minute hour day_of_month month_of_year day_of_week")
        
        schedule_kwargs = dict(
            zip(("minute", "hour", "day_of_month", "month_of_year", "day_of_week"), parts, strict=True)
        )
        if not timezone_name or timezone_name == "UTC":
            return crontab(**schedule_kwargs)
        return NamedTimezoneCrontab(**schedule_kwargs, timezone_name=timezone_name)
