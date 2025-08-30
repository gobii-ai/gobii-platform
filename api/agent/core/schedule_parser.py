from __future__ import annotations
from celery.schedules import crontab, schedule as celery_schedule
from datetime import timedelta
import re


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
    UNIT_MAP = {
        "s": "seconds",
        "m": "minutes",
        "h": "hours",
        "d": "days",
    }

    @classmethod
    def parse(cls, schedule_str: str) -> celery_schedule | None:
        """
        Parses a schedule string and returns a celery schedule object.
        Returns None if schedule is to be disabled.
        Raises ValueError for invalid formats.
        """
        if not schedule_str:
            return None

        schedule_str = schedule_str.strip()

        if schedule_str in cls.SHORTHANDS:
            schedule_str = cls.SHORTHANDS[schedule_str]

        if schedule_str == "@reboot":
            raise ValueError("Unsupported schedule format: @reboot")

        interval_match = cls.INTERVAL_REGEX.match(schedule_str)
        if interval_match:
            interval_str = interval_match.group(2).strip()
            return cls._parse_interval(interval_str)

        return cls._parse_crontab(schedule_str)

    @classmethod
    def _parse_interval(cls, interval_str: str) -> celery_schedule:
        """Parses an interval string like '30m' or '2h 30m'."""
        total_seconds = 0
        parts = interval_str.split()
        for part in parts:
            if not part:
                continue
            
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
    def _parse_crontab(cls, schedule_str: str) -> crontab:
        """Parses a crontab string."""
        parts = schedule_str.split()
        if len(parts) != 5:
            raise ValueError(
                "Invalid cron format. Expected 5 parts: "
                "(minute hour day_of_month month_of_year day_of_week)"
            )
        
        minute, hour, day_of_month, month_of_year, day_of_week = parts
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            day_of_week=day_of_week,
        ) 