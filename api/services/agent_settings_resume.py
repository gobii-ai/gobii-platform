import json
from decimal import Decimal, InvalidOperation

from django.db import transaction

from api.agent.tasks import process_agent_events_task
from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentSystemStep


def _format_credit_limit(value) -> str:
    if value is None:
        return "unlimited"

    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    if numeric == numeric.to_integral_value():
        return str(int(numeric))

    normalized = numeric.normalize()
    return format(normalized, "f")


def queue_settings_change_resume(
    agent: PersistentAgent,
    *,
    daily_credit_limit_changed: bool = False,
    previous_daily_credit_limit=None,
    preferred_llm_tier_changed: bool = False,
    previous_preferred_llm_tier_key: str | None = None,
    source: str = "unknown",
) -> bool:
    if not daily_credit_limit_changed and not preferred_llm_tier_changed:
        return False

    notes_payload: dict[str, object] = {
        "source": source,
        "changes": {},
    }
    change_fragments: list[str] = []

    if daily_credit_limit_changed:
        previous_limit = _format_credit_limit(previous_daily_credit_limit)
        current_limit = _format_credit_limit(agent.daily_credit_limit)
        notes_payload["changes"]["daily_credit_limit"] = {
            "previous": previous_limit,
            "current": current_limit,
        }
        change_fragments.append(
            f"Daily credit soft target changed from {previous_limit} to {current_limit}."
        )

    if preferred_llm_tier_changed:
        previous_tier = str(previous_preferred_llm_tier_key or "standard").strip() or "standard"
        current_tier = str(getattr(getattr(agent, "preferred_llm_tier", None), "key", "standard"))
        notes_payload["changes"]["preferred_llm_tier"] = {
            "previous": previous_tier,
            "current": current_tier,
        }
        change_fragments.append(
            f"Intelligence level changed from {previous_tier} to {current_tier}."
        )

    description = (
        "Agent settings updated. "
        + " ".join(change_fragments)
        + " Resume immediately with the updated configuration."
    )
    step = PersistentAgentStep.objects.create(
        agent=agent,
        description=description,
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
        notes=json.dumps(notes_payload, separators=(",", ":"), sort_keys=True),
    )

    transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.id)))
    return True
