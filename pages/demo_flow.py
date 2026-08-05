"""Local demo of the template-intake new-customer flow.

Everything here is gated behind GOBII_DEMO_INTAKE=1 (env var) and is additive:
with the flag unset, production behavior is untouched. The intake schemas below
are the code-level spec for the future PersistentAgentTemplate.intake_schema
field — kept in code for the simulation so no migration is needed yet.

Question kinds (by who can know the answer):
  sample-text     — ghost example; untouched -> assumed, agent confirms in chat
  capture-tags    — rapid-add, user's words verbatim; empty -> agent asks in chat
  template-options— options the template author legitimately knows; real defaults
  delivery        — fixed ladder: email -> sheet -> integrated system -> other
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEMO_SESSION_FLAG = "demo_intake_flow"
DEMO_ANSWERS_KEY = "demo_intake_answers"

from pages.template_intake import SKIP_VALUE  # noqa: F401


def demo_intake_enabled() -> bool:
    return os.environ.get("GOBII_DEMO_INTAKE") == "1"


def demo_real_llm() -> bool:
    """Real-run validation mode: keep the simulated brief/trial screens, but let
    the actual processing loop run (active routing profile, e.g. DeepSeek V4
    Flash via OpenRouter) instead of seeding canned replies."""
    return os.environ.get("GOBII_DEMO_REAL") == "1"


def launch_detached_processing(agent_id: str) -> None:
    """Run the agent loop in a detached process.

    Local-only shim for GOBII_DEMO_REAL: eager Celery would otherwise run the
    full LLM loop inside the HTTP request ("Preparing your agent…" until the
    connection times out). A real broker/worker makes this unnecessary; this
    exists only so the local sim behaves like production's async processing.
    """
    import threading

    def _run() -> None:
        try:
            from api.agent.core.event_processing import process_agent_events

            process_agent_events(agent_id)
        except Exception:
            logger.exception("Demo flow: background processing failed for %s", agent_id)

    # A thread (not a subprocess) so the in-memory channel layer and processing
    # state live in the same process as the websocket consumers — the open tab
    # sees "working" and receives messages live, like production.
    threading.Thread(target=_run, name=f"demo-loop-{agent_id[:8]}", daemon=True).start()
    logger.info("Demo flow: background processing thread launched for %s", agent_id)


def demo_session_active(request) -> bool:
    return demo_intake_enabled() and bool(request.session.get(DEMO_SESSION_FLAG))


# Schema registry and brief/charter/briefing builders live in the real
# (ungated) module now; the demo imports them for its canned-reply path.
from pages.template_intake import (  # noqa: F401  (re-exported for demo callers)
    INTAKE_SCHEMAS,
    TEMPLATE_SYSTEM_SKILLS,
    _answer,
    build_brief_message,
    build_briefing_payload,
    build_charter_override,
    get_intake_schema,
    get_template_system_skills,
)


def build_demo_agent_replies(answers: dict) -> list[str]:
    role = _answer(answers, "role", "the role")
    must = _answer(answers, "must", "")
    open_items = not must
    first = (
        f"Hi — I've got your brief and I'm starting the first sweep for **{role}** now. "
        "Your first batch lands by email **Monday 8:00 AM** — if you'd rather have candidates "
        "straight in Greenhouse or a sheet, just say so and I'll set it up."
    )
    if open_items:
        first += " One thing before I go deep: what makes a candidate qualified for you — skills, seniority, anything disqualifying?"
    second = (
        "First pass done — 214 profiles matched the search, screened down to a strong shortlist. "
        "Three to start:\n\n"
        "1. **M. Kessler** — Go, K8s, 7 yrs · ex-Plaid · Denver (remote) · *94% fit*\n"
        "2. **R. Santos** — distributed systems · Stripe · Austin · *91% fit*\n"
        "3. **A. Liu** — platform lead · Brex · remote US · *89% fit*\n\n"
        "The full screened batch of 20 arrives in Monday's 8:00 AM email. "
        "Want me to bias the next pass toward fintech backgrounds, or keep it broad?"
    )
    return [first, second]


def grant_demo_entitlement(agent) -> None:
    """Make the simulated trial coherent: entitle the user (freemium-grandfathered,
    a real product path) and clear the signup-preview state so no upsell panels
    contradict the 'trial started' fiction."""
    from datetime import timedelta

    from django.utils import timezone

    from api.models import GrantTypeChoices, PersistentAgent, PlanNamesChoices, TaskCredit, UserFlags

    try:
        from api.models import UserQuota

        quota, _ = UserQuota.objects.get_or_create(user=agent.user)
        if quota.agent_limit < 100:
            quota.agent_limit = 100
            quota.save(update_fields=["agent_limit"])
        # Verified email unlocks the agent's external-comms leg (question
        # follow-ups by email) — signup verification is skipped locally.
        try:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.update_or_create(
                user=agent.user,
                email=agent.user.email,
                defaults={"verified": True, "primary": True},
            )
        except Exception:
            logger.exception("Demo flow: failed to verify email for %s", agent.user.email)
        flags = UserFlags.ensure_for_user(agent.user)
        if not flags.is_freemium_grandfathered:
            flags.is_freemium_grandfathered = True
            flags.save(update_fields=["is_freemium_grandfathered"])
        PersistentAgent.objects.filter(id=agent.id).update(
            signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
        )
        # A "trial" account needs credits to work with — mirror a plan grant so
        # real-LLM demo loops don't halt on insufficient credits.
        now = timezone.now()
        TaskCredit.objects.create(
            user=agent.user,
            credits=500,
            granted_date=now,
            expiration_date=now + timedelta(days=30),
            plan=PlanNamesChoices.STARTUP,
            additional_task=False,
            grant_type=GrantTypeChoices.PLAN,
            voided=False,
        )
    except Exception:
        logger.exception("Demo flow: failed to grant demo entitlement for %s", agent.id)


def seed_demo_agent_replies(agent, conversation, agent_endpoint, user_endpoint, answers: dict) -> None:
    """Create canned outbound messages so the real chat UI shows a working agent.

    No LLM, no tools, no processing loop — plain PersistentAgentMessage rows
    rendered by the production timeline exactly like real agent output.
    """
    from api.models import PersistentAgent, PersistentAgentMessage

    try:
        for body in build_demo_agent_replies(answers or {}):
            PersistentAgentMessage.objects.create(
                is_outbound=True,
                from_endpoint=agent_endpoint,
                to_endpoint=user_endpoint,
                conversation=conversation,
                body=body,
                owner_agent=agent,
            )
        grant_demo_entitlement(agent)
    except Exception:
        logger.exception("Demo flow: failed to seed canned agent replies for %s", agent.id)
