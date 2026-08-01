import logging
from django.conf import settings
from waffle import switch_is_active

from constants.plans import PlanNames
from constants.feature_flags import AGENT_CRON_THROTTLE
from api.models import PersistentAgent, PersistentAgentEmailFooter
from api.services.cron_throttle import (
    build_upgrade_link,
    claim_pending_cron_throttle_footer,
    clear_pending_cron_throttle_footer,
    consume_pending_cron_throttle_footer,
    evaluate_free_plan_cron_throttle,
    has_pending_cron_throttle_footer,
    select_cron_throttle_footer,
)

logger = logging.getLogger(__name__)


def append_footer_if_needed(
    agent: PersistentAgent | None,
    html_body: str,
    plaintext_body: str,
) -> tuple[str, str]:
    updated_html, updated_plain, _ = _append_footer(
        agent,
        html_body,
        plaintext_body,
        consume_throttle_footer=True,
    )
    return updated_html, updated_plain


def append_footer_for_review(
    agent: PersistentAgent | None,
    html_body: str,
    plaintext_body: str,
    *,
    include_throttle_footer: bool,
) -> tuple[str, str, bool]:
    """Render a reviewable footer without consuming one-time delivery state."""
    return _append_footer(
        agent,
        html_body,
        plaintext_body,
        consume_throttle_footer=False,
        include_reviewed_throttle_footer=include_throttle_footer,
    )


def reviewed_throttle_footer_is_pending(agent: PersistentAgent | None) -> bool:
    if agent is None or not switch_is_active(AGENT_CRON_THROTTLE) or not _should_apply_footer(agent):
        return False
    try:
        return has_pending_cron_throttle_footer(str(agent.id))
    except Exception:
        logger.debug("Failed checking pending throttle footer for agent %s", agent.id, exc_info=True)
        return False


def consume_reviewed_throttle_footer(agent: PersistentAgent | None) -> None:
    if agent is None:
        return
    try:
        consume_pending_cron_throttle_footer(
            str(agent.id),
            ttl_seconds=_notice_ttl_seconds(),
        )
    except Exception:
        logger.debug("Failed consuming reviewed throttle footer for agent %s", agent.id, exc_info=True)


def _append_footer(
    agent: PersistentAgent | None,
    html_body: str,
    plaintext_body: str,
    *,
    consume_throttle_footer: bool,
    include_reviewed_throttle_footer: bool = False,
) -> tuple[str, str, bool]:
    """
    Append a configured footer to the provided HTML/plaintext bodies when the
    owning agent is associated with a free plan (or an organization without seats).
    """
    if not agent:
        return html_body, plaintext_body, False

    if not _should_apply_footer(agent):
        if consume_throttle_footer and switch_is_active(AGENT_CRON_THROTTLE):
            try:
                clear_pending_cron_throttle_footer(str(agent.id))
            except Exception:
                logger.debug(
                    "Failed clearing pending throttle footer for agent %s after footer no longer applies.",
                    agent.id,
                    exc_info=True,
                )
        return html_body, plaintext_body, False

    if include_reviewed_throttle_footer:
        throttle_footer = _build_throttle_footer(agent)
    elif consume_throttle_footer:
        throttle_footer = _consume_throttle_footer_if_pending(agent)
    else:
        throttle_footer = None
    if throttle_footer is not None:
        updated_html = _append_section(html_body, throttle_footer.html_content)
        updated_plain = _append_section(plaintext_body, throttle_footer.text_content, separator="\n\n")
        return updated_html, updated_plain, True

    footer = _pick_random_footer()
    if footer is None:
        return html_body, plaintext_body, False

    updated_html = _append_section(html_body, footer.html_content)
    updated_plain = _append_section(plaintext_body, footer.text_content, separator="\n\n")

    return updated_html, updated_plain, False


def _consume_throttle_footer_if_pending(agent: PersistentAgent):
    if not switch_is_active(AGENT_CRON_THROTTLE):
        return None

    try:
        claimed = claim_pending_cron_throttle_footer(
            str(agent.id),
            ttl_seconds=_notice_ttl_seconds(),
        )
    except Exception:
        logger.debug("Failed consuming throttle footer pending flag for agent %s", agent.id, exc_info=True)
        return None
    return _build_throttle_footer(agent) if claimed else None


def _build_throttle_footer(agent: PersistentAgent):
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

    return select_cron_throttle_footer(
        agent_name=agent.name,
        effective_interval_seconds=effective_interval_seconds,
        upgrade_link=upgrade_link,
    )


def _notice_ttl_seconds() -> int:
    return max(1, int(settings.AGENT_CRON_THROTTLE_NOTICE_TTL_DAYS) * 86400)


def _should_apply_footer(agent: PersistentAgent) -> bool:
    """Return True when the owning agent should include a footer."""
    owner = agent.organization or agent.user
    if owner is None:
        return False

    billing = getattr(owner, "billing", None)
    if billing is None:
        return True

    subscription = str(billing.subscription or "").strip().lower()
    if subscription == PlanNames.FREE:
        return True

    if agent.organization_id:
        return (billing.purchased_seats or 0) <= 0

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
    if (
        separator == "\n"
        and existing.rstrip().lower().endswith("</table>")
        and addition.lower().startswith("<table")
    ):
        return f"{existing}<br />{addition}"
    return f"{existing}{separator}{addition}"
