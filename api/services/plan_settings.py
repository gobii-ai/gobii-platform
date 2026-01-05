from typing import Any, Optional, Tuple

from constants.plans import PlanNames
from util.subscription_helper import get_owner_plan


def select_plan_settings_payload(
    settings_map: dict[str, dict[str, dict]],
    plan_version_id: Optional[str],
    plan_name: Optional[str],
    *,
    default_plan: str = PlanNames.FREE,
) -> dict:
    """Pick a plan settings payload with plan_version -> plan_name fallback."""
    by_plan_version = settings_map.get("by_plan_version", {})
    by_plan_name = settings_map.get("by_plan_name", {})

    if plan_version_id:
        payload = by_plan_version.get(str(plan_version_id))
        if payload is not None:
            return payload

    normalized_plan = (plan_name or default_plan).lower()
    payload = by_plan_name.get(normalized_plan)
    if payload is not None:
        return payload

    return by_plan_name.get(default_plan) or {}


def resolve_owner_plan_identifiers(owner, *, logger: Any = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (plan_name, plan_version_id) for an owner with guarded logging."""
    if not owner:
        return None, None
    try:
        plan = get_owner_plan(owner)
        plan_name = plan.get("legacy_plan_code") or plan.get("id")
        plan_version_id = plan.get("plan_version_id")
        return plan_name, plan_version_id
    except Exception as exc:
        if logger:
            logger.warning("Failed to resolve plan for owner %s: %s", owner, exc, exc_info=True)
        return None, None
