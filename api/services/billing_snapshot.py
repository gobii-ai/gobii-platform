import logging
from typing import Any

from django.core.exceptions import AppRegistryNotReady, ObjectDoesNotExist
from django.db import OperationalError, ProgrammingError

from billing.plan_resolver import get_owner_plan_context
from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanSlugsChoices
from util.user_behavior import is_owner_currently_in_trial

logger = logging.getLogger(__name__)

_KNOWN_PLAN_SLUGS = {choice.value for choice in PlanSlugsChoices}
_BILLING_SNAPSHOT_ERRORS = (
    AppRegistryNotReady,
    LookupError,
    ObjectDoesNotExist,
    OperationalError,
    ProgrammingError,
    TypeError,
    ValueError,
)


def _normalize_plan_slug(plan_context: dict[str, Any] | None) -> str | None:
    if not plan_context:
        return None

    raw_slug = plan_context.get("slug")
    if not raw_slug:
        raw_id = str(plan_context.get("id") or "").strip().lower()
        raw_slug = PLAN_SLUG_BY_LEGACY_CODE.get(raw_id, raw_id)

    normalized_slug = str(raw_slug or "").strip().lower()
    if normalized_slug in _KNOWN_PLAN_SLUGS:
        return normalized_slug
    return None


def get_billing_snapshot_for_owner(owner) -> dict[str, Any]:
    snapshot = {
        "billing_plan": None,
        "billing_is_trial": None,
    }
    owner_id = getattr(owner, "id", None) or getattr(owner, "pk", None)
    if owner is None or owner_id is None:
        return snapshot

    try:
        snapshot["billing_plan"] = _normalize_plan_slug(get_owner_plan_context(owner))
    except _BILLING_SNAPSHOT_ERRORS:
        # Billing snapshots are metadata for reporting and should never block
        # task/completion creation if plan resolution is temporarily unavailable.
        logger.warning(
            "Failed to resolve billing plan snapshot for owner %s",
            owner_id,
            exc_info=True,
        )

    try:
        snapshot["billing_is_trial"] = is_owner_currently_in_trial(owner)
    except _BILLING_SNAPSHOT_ERRORS:
        logger.warning(
            "Failed to resolve trial snapshot for owner %s",
            owner_id,
            exc_info=True,
        )

    return snapshot
