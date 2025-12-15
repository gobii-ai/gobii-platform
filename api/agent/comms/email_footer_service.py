import logging
from django.conf import settings
from waffle import switch_is_active

from constants.plans import PlanNames
from constants.feature_flags import AGENT_CRON_THROTTLE
from util.subscription_helper import get_owner_plan
from config.redis_client import get_redis_client

from api.models import PersistentAgent, PersistentAgentEmailFooter
from api.services.cron_throttle import (
    cron_throttle_footer_cooldown_key,
    cron_throttle_pending_footer_key,
    evaluate_free_plan_cron_throttle,
    build_upgrade_link,
    select_cron_throttle_footer,
)

logger = logging.getLogger(__name__)


def append_footer_if_needed(
    agent: PersistentAgent | None,
    html_body: str,
    plaintext_body: str,
) -> tuple[str, str]:
    """
    Append a configured footer to the provided HTML/plaintext bodies when the
    owning agent is associated with a free plan (or an organization without seats).
    """
    if not agent:
        return html_body, plaintext_body

    if not _should_apply_footer(agent):
        if switch_is_active(AGENT_CRON_THROTTLE):
            try:
                redis_client = get_redis_client()
                redis_client.delete(cron_throttle_pending_footer_key(str(agent.id)))
            except Exception:
                logger.debug(
                    "Failed clearing pending throttle footer for agent %s after footer no longer applies.",
                    agent.id,
                    exc_info=True,
                )
        return html_body, plaintext_body

    throttle_footer = _consume_throttle_footer_if_pending(agent)
    if throttle_footer is not None:
        updated_html = _append_section(html_body, throttle_footer.html_content)
        updated_plain = _append_section(plaintext_body, throttle_footer.text_content, separator="\n\n")
        return updated_html, updated_plain

    footer = _pick_random_footer()
    if footer is None:
        return html_body, plaintext_body

    updated_html = _append_section(html_body, footer.html_content)
    updated_plain = _append_section(plaintext_body, footer.text_content, separator="\n\n")

    return updated_html, updated_plain


def _consume_throttle_footer_if_pending(agent: PersistentAgent):
    if not switch_is_active(AGENT_CRON_THROTTLE):
        return None

    try:
        redis_client = get_redis_client()
    except Exception:
        logger.debug("Failed to fetch redis client for throttle footer check", exc_info=True)
        return None

    pending_key = cron_throttle_pending_footer_key(str(agent.id))
    try:
        pending = bool(redis_client.get(pending_key))
    except Exception:
        logger.debug("Throttle footer pending check failed for agent %s", agent.id, exc_info=True)
        return None

    if not pending:
        return None

    effective_interval_seconds = None
    schedule_str = (getattr(agent, "schedule", None) or "").strip()
    try:
        decision = evaluate_free_plan_cron_throttle(agent, schedule_str)
        if decision.throttling_applies:
            effective_interval_seconds = decision.effective_interval_seconds
    except Exception:
        logger.debug("Failed to compute cron throttle interval for agent %s", agent.id, exc_info=True)

    try:
        upgrade_link = build_upgrade_link()
    except Exception:
        upgrade_link = "/subscribe/pro/"

    footer = select_cron_throttle_footer(
        agent_name=agent.name,
        effective_interval_seconds=effective_interval_seconds,
        upgrade_link=upgrade_link,
    )

    try:
        redis_client.delete(pending_key)
        ttl_days = int(getattr(settings, "AGENT_CRON_THROTTLE_NOTICE_TTL_DAYS", 7))
        ttl_seconds = max(1, ttl_days * 86400)
        redis_client.set(
            cron_throttle_footer_cooldown_key(str(agent.id)),
            "1",
            ex=ttl_seconds,
        )
    except Exception:
        logger.debug("Failed to consume throttle footer pending flag for agent %s", agent.id, exc_info=True)

    return footer


def _should_apply_footer(agent: PersistentAgent) -> bool:
    """Return True when the owning agent should include a footer."""
    owner = agent.organization or agent.user
    if owner is None:
        return False

    try:
        plan = get_owner_plan(owner) or {}
    except Exception:
        logger.exception("Unable to determine plan for agent %s", agent.id)
        return False

    plan_id = str(plan.get("id") or "").lower()
    if plan_id == PlanNames.FREE:
        return True

    if agent.organization_id:
        billing = getattr(agent.organization, "billing", None)
        seats = getattr(billing, "purchased_seats", 0) if billing else 0
        if seats <= 0:
            return True

    return False


def _pick_random_footer() -> PersistentAgentEmailFooter | None:
    """Return a random active footer entry."""
    try:
        return (
            PersistentAgentEmailFooter.objects.filter(is_active=True)
            .order_by("?")
            .first()
        )
    except Exception:
        logger.exception("Failed selecting persistent agent email footer")
        return None


def _append_section(existing: str, addition: str, *, separator: str = "\n") -> str:
    existing = existing or ""
    addition = (addition or "").strip()
    if not addition:
        return existing
    if not existing.strip():
        return addition
    return f"{existing}{separator}{addition}"
