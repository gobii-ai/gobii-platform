import logging
import math
import random
from dataclasses import dataclass
from typing import Optional

from celery.schedules import crontab, schedule as celery_schedule
from django.conf import settings
from django.utils import timezone

from api.agent.core.schedule_parser import ScheduleParser
from api.services.schedule_enforcement import cron_interval_seconds
from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CronThrottleDecision:
    throttling_applies: bool
    allow_execution: bool
    stage: int
    base_interval_seconds: int
    effective_interval_seconds: int
    reason: str


def cron_throttle_gate_key(agent_id: str) -> str:
    return f"cron-throttle:agent:{agent_id}"


def cron_throttle_pending_footer_key(agent_id: str) -> str:
    """Marks that the next outbound comms should include a throttle footer."""
    return f"cron-throttle:pending-footer:{agent_id}"


def cron_throttle_footer_cooldown_key(agent_id: str) -> str:
    """Dedupe key to avoid repeating throttle footers too frequently."""
    return f"cron-throttle:footer-cooldown:{agent_id}"


def is_free_or_seatless_agent(agent) -> bool:
    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    if owner is None:
        return False

    try:
        plan = get_owner_plan(owner) or {}
    except Exception:
        logger.exception("Unable to determine plan for agent %s", getattr(agent, "id", None))
        return False

    plan_id = str(plan.get("id") or "").lower()
    if plan_id == PlanNames.FREE:
        return True

    if getattr(agent, "organization_id", None):
        billing = getattr(getattr(agent, "organization", None), "billing", None)
        seats = getattr(billing, "purchased_seats", 0) if billing else 0
        if seats <= 0:
            return True

    return False


def _parse_schedule_interval_seconds(schedule_str: str) -> Optional[int]:
    if not schedule_str:
        return None

    try:
        schedule_obj = ScheduleParser.parse(schedule_str)
    except Exception:
        logger.debug("Failed to parse schedule string %r", schedule_str, exc_info=True)
        return None

    if schedule_obj is None:
        return None

    if isinstance(schedule_obj, celery_schedule):
        try:
            run_every = getattr(schedule_obj, "run_every", None)
            seconds = run_every.total_seconds() if hasattr(run_every, "total_seconds") else float(run_every)
            return max(1, int(math.ceil(seconds)))
        except Exception:
            logger.debug("Failed to parse interval schedule %r", schedule_str, exc_info=True)
            return None

    if isinstance(schedule_obj, crontab):
        try:
            seconds = cron_interval_seconds(schedule_obj)
            return max(1, int(math.ceil(float(seconds))))
        except Exception:
            logger.debug("Failed to compute cron interval for %r", schedule_str, exc_info=True)
            return None

    return None


def _agent_age_days(agent, *, now) -> float:
    created_at = getattr(agent, "created_at", None)
    if not created_at:
        return 0.0
    try:
        return max(0.0, (now - created_at).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _throttle_stage(age_days: float, *, start_age_days: int, stage_days: int) -> int:
    if age_days < float(start_age_days):
        return 0

    stage_days = max(int(stage_days or 0), 1)
    elapsed = age_days - float(start_age_days)
    return int(elapsed // float(stage_days)) + 1  # start at 2× right away


def _effective_interval_seconds(*, base_interval_seconds: int, stage: int, max_interval_seconds: int) -> int:
    max_interval_seconds = max(int(max_interval_seconds or 0), 1)
    interval = max(int(base_interval_seconds or 0), 1)

    for _ in range(max(stage, 0)):
        if interval >= max_interval_seconds:
            return max_interval_seconds
        interval *= 2

    return min(interval, max_interval_seconds)


def evaluate_free_plan_cron_throttle(agent, schedule_str: str, *, now=None) -> CronThrottleDecision:
    now = now or timezone.now()

    if not is_free_or_seatless_agent(agent):
        return CronThrottleDecision(
            throttling_applies=False,
            allow_execution=True,
            stage=0,
            base_interval_seconds=0,
            effective_interval_seconds=0,
            reason="plan_not_throttled",
        )

    base_interval_seconds = _parse_schedule_interval_seconds(schedule_str)
    if base_interval_seconds is None:
        return CronThrottleDecision(
            throttling_applies=False,
            allow_execution=True,
            stage=0,
            base_interval_seconds=0,
            effective_interval_seconds=0,
            reason="schedule_unparseable",
        )

    start_age_days = int(getattr(settings, "AGENT_CRON_THROTTLE_START_AGE_DAYS", 16))
    stage_days = int(getattr(settings, "AGENT_CRON_THROTTLE_STAGE_DAYS", 7))
    max_interval_days = int(getattr(settings, "AGENT_CRON_THROTTLE_MAX_INTERVAL_DAYS", 30))
    max_interval_seconds = max_interval_days * 86400

    age_days = _agent_age_days(agent, now=now)
    stage = _throttle_stage(age_days, start_age_days=start_age_days, stage_days=stage_days)
    if stage <= 0:
        return CronThrottleDecision(
            throttling_applies=False,
            allow_execution=True,
            stage=0,
            base_interval_seconds=base_interval_seconds,
            effective_interval_seconds=base_interval_seconds,
            reason="age_below_threshold",
        )

    effective_interval = _effective_interval_seconds(
        base_interval_seconds=base_interval_seconds,
        stage=stage,
        max_interval_seconds=max_interval_seconds,
    )

    return CronThrottleDecision(
        throttling_applies=True,
        allow_execution=False,  # caller decides via gate
        stage=stage,
        base_interval_seconds=base_interval_seconds,
        effective_interval_seconds=effective_interval,
        reason="throttled",
    )


def format_interval_seconds(seconds: int) -> str:
    seconds = max(int(seconds or 0), 0)
    if seconds <= 0:
        return "0 seconds"

    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


def build_upgrade_link() -> str:
    base = (getattr(settings, "PUBLIC_SITE_URL", "") or "").strip().rstrip("/")
    if base:
        return f"{base}/subscribe/pro/"

    return "/subscribe/pro/"


@dataclass(frozen=True)
class ThrottleFooterContent:
    html_content: str
    text_content: str


def select_cron_throttle_footer(
    *,
    agent_name: str,
    effective_interval_seconds: Optional[int],
    upgrade_link: str,
) -> ThrottleFooterContent:
    """Return a friendly, upsell footer to explain free-plan throttling."""
    interval_text = (
        format_interval_seconds(int(effective_interval_seconds))
        if effective_interval_seconds
        else None
    )

    variants: list[ThrottleFooterContent] = [
        ThrottleFooterContent(
            html_content=(
                "<p>🥺 Quick heads-up: on the Free plan, scheduled runs can be a bit slower to save resources.</p>"
                f"<p>Want <strong>{agent_name}</strong> to run on its full schedule? "
                f"<a href=\"{upgrade_link}\">Upgrade to Pro</a> (and remove this note).</p>"
            ),
            text_content=(
                "🥺 Quick heads-up: on the Free plan, scheduled runs can be a bit slower to save resources.\n\n"
                f"Want {agent_name} to run on its full schedule? Upgrade to Pro (and remove this note): {upgrade_link}"
            ),
        ),
        ThrottleFooterContent(
            html_content=(
                f"<p>🥺 {agent_name} ran a bit slower this time because you’re on the Free plan.</p>"
                f"<p><a href=\"{upgrade_link}\">Upgrade to Pro</a> to restore full speed and remove this footer.</p>"
            ),
            text_content=(
                f"🥺 {agent_name} ran a bit slower this time because you’re on the Free plan.\n\n"
                f"Upgrade to Pro to restore full speed and remove this footer: {upgrade_link}"
            ),
        ),
        ThrottleFooterContent(
            html_content=(
                "<p>🥺 Free plan throttling: scheduled runs may be delayed a bit to keep things fair.</p>"
                + (
                    f"<p>Right now, {agent_name} may run about once every <strong>{interval_text}</strong>.</p>"
                    if interval_text
                    else ""
                )
                + f"<p><a href=\"{upgrade_link}\">Upgrade to Pro</a> to restore the full schedule.</p>"
            ),
            text_content=(
                "🥺 Free plan throttling: scheduled runs may be delayed a bit to keep things fair."
                + (f" Right now, {agent_name} may run about once every {interval_text}." if interval_text else "")
                + f"\n\nUpgrade to Pro to restore the full schedule: {upgrade_link}"
            ),
        ),
    ]

    return random.choice(variants)


def select_cron_throttle_sms_suffix(
    *,
    agent_name: str,
    effective_interval_seconds: Optional[int],
    upgrade_link: str,
) -> str:
    interval_text = (
        format_interval_seconds(int(effective_interval_seconds))
        if effective_interval_seconds
        else None
    )

    variants = [
        (
            f"🥺 Heads up: {agent_name} scheduled runs can be slower on the Free plan. "
            f"Upgrade to Pro to restore full speed: {upgrade_link}"
        ),
        (
            f"🥺 {agent_name} is throttled a bit on the Free plan to save resources"
            + (f" (about every {interval_text})" if interval_text else "")
            + f". Upgrade to Pro: {upgrade_link}"
        ),
    ]
    return random.choice(variants)
