from __future__ import annotations

import logging
from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan

from api.models import PersistentAgent, PersistentAgentEmailFooter

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
        return html_body, plaintext_body

    footer = _pick_random_footer()
    if footer is None:
        return html_body, plaintext_body

    updated_html = _append_section(html_body, footer.html_content)
    updated_plain = _append_section(plaintext_body, footer.text_content, separator="\n\n")

    return updated_html, updated_plain


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
