import logging
from typing import Any

from django.apps import apps

from config.plans import PLAN_CONFIG
from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames

logger = logging.getLogger(__name__)

ENTITLEMENT_KEY_MAP = {
    "max_agents": "agent_limit",
    "monthly_task_credits": "monthly_task_credits",
    "api_rate_limit": "api_rate_limit",
    "max_contacts_per_agent": "max_contacts_per_agent",
    "credits_per_seat": "credits_per_seat",
    "additional_tasks_allowed": "additional_tasks_allowed",
    "additional_task_limit_default": "additional_task_limit_default",
    "has_dedicated_ip": "has_dedicated_ip",
}


def _get_model(app_label: str, model_name: str):
    return apps.get_model(app_label, model_name)


def _normalize_code(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip().lower()


def get_plan_version_by_legacy_code(legacy_code: str | None):
    if not legacy_code:
        return None
    PlanVersion = _get_model("api", "PlanVersion")
    return (
        PlanVersion.objects
        .select_related("plan")
        .filter(legacy_plan_code__iexact=str(legacy_code).strip())
        .first()
    )


def get_plan_version_by_price_id(
    price_id: str | None,
    *,
    kind: str | None = None,
    plan_version=None,
    plan_id: str | None = None,
    owner_type: str | None = None,
):
    if not price_id:
        return None
    PlanVersionPrice = _get_model("api", "PlanVersionPrice")
    qs = (
        PlanVersionPrice.objects
        .select_related("plan_version", "plan_version__plan")
        .filter(price_id=str(price_id).strip())
    )
    if kind:
        qs = qs.filter(kind=kind)
    if plan_version:
        plan_version_id = getattr(plan_version, "id", plan_version)
        qs = qs.filter(plan_version_id=plan_version_id)
    else:
        normalized_plan = _normalize_code(plan_id)
        if normalized_plan:
            plan_slug = PLAN_SLUG_BY_LEGACY_CODE.get(normalized_plan, normalized_plan)
            qs = qs.filter(plan_version__plan__slug__iexact=plan_slug)
        if owner_type:
            is_org = owner_type == "organization"
            qs = qs.filter(plan_version__plan__is_org=is_org)
        qs = qs.order_by(
            "-plan_version__is_active_for_new_subs",
            "-plan_version__created_at",
        )
    match = qs.first()
    return match.plan_version if match else None


def get_plan_version_by_product_id(product_id: str | None, *, kind: str | None = None):
    if not product_id:
        return None
    PlanVersionPrice = _get_model("api", "PlanVersionPrice")
    qs = (
        PlanVersionPrice.objects
        .select_related("plan_version", "plan_version__plan")
        .filter(product_id=str(product_id).strip())
    )
    if kind:
        qs = qs.filter(kind=kind)
    qs = qs.order_by(
        "-plan_version__is_active_for_new_subs",
        "-plan_version__created_at",
    )
    match = qs.first()
    return match.plan_version if match else None


def get_owner_plan_version(owner):
    billing = _get_owner_billing(owner)
    if not billing:
        return None

    plan_version = getattr(billing, "plan_version", None)
    if plan_version:
        return plan_version

    legacy_code = getattr(billing, "subscription", None)
    return get_plan_version_by_legacy_code(legacy_code)


def _get_owner_billing(owner):
    if owner is None:
        return None

    billing = getattr(owner, "billing", None)
    if billing:
        if getattr(billing, "plan_version_id", None) is not None:
            return billing
        # Refresh from DB to pick up plan_version backfills or updates.
        billing = None

    Organization = _get_model("api", "Organization")
    if isinstance(owner, Organization):
        OrganizationBilling = _get_model("api", "OrganizationBilling")
        return (
            OrganizationBilling.objects
            .select_related("plan_version", "plan_version__plan")
            .filter(organization_id=owner.id)
            .first()
        )

    UserBilling = _get_model("api", "UserBilling")
    return (
        UserBilling.objects
        .select_related("plan_version", "plan_version__plan")
        .filter(user_id=owner.id)
        .first()
    )


def _plan_context_base(plan_version, legacy_code: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    legacy_key = _normalize_code(legacy_code)
    if legacy_key:
        base = dict(PLAN_CONFIG.get(legacy_key, {}))

    plan_slug = None
    if plan_version is not None:
        plan_slug = getattr(getattr(plan_version, "plan", None), "slug", None)
    if not plan_slug and legacy_key:
        plan_slug = PLAN_SLUG_BY_LEGACY_CODE.get(legacy_key, legacy_key)

    plan_id = legacy_key or plan_slug or PlanNames.FREE
    base.setdefault("id", plan_id)
    base.setdefault("slug", plan_slug or plan_id)

    if plan_version is None:
        return base

    base["plan_version_id"] = str(plan_version.id)
    base["plan_version_code"] = plan_version.version_code
    base["legacy_plan_code"] = legacy_code or plan_version.legacy_plan_code
    base["display_name"] = plan_version.display_name
    base.setdefault("name", plan_version.display_name)
    base["tagline"] = plan_version.tagline
    base["description"] = plan_version.description
    base["marketing_features"] = plan_version.marketing_features
    base["org"] = bool(getattr(plan_version.plan, "is_org", False))
    return base


def _entitlement_value(entitlement, value_row) -> Any:
    value_type = getattr(entitlement, "value_type", None)
    if value_type == "int":
        return value_row.value_int
    if value_type == "decimal":
        return value_row.value_decimal
    if value_type == "bool":
        return value_row.value_bool
    if value_type == "text":
        return value_row.value_text
    if value_type == "json":
        return value_row.value_json

    # Fallback to first non-null field
    for field in (
        value_row.value_int,
        value_row.value_decimal,
        value_row.value_bool,
        value_row.value_text,
        value_row.value_json,
    ):
        if field is not None:
            return field
    return None


def _apply_entitlements(plan_context: dict[str, Any], plan_version) -> dict[str, Any]:
    PlanVersionEntitlement = _get_model("api", "PlanVersionEntitlement")

    entitlements = (
        PlanVersionEntitlement.objects
        .select_related("entitlement")
        .filter(plan_version=plan_version)
    )

    for value_row in entitlements:
        entitlement = value_row.entitlement
        key = entitlement.key
        value = _entitlement_value(entitlement, value_row)
        if value is None:
            continue
        mapped_key = ENTITLEMENT_KEY_MAP.get(key, key)
        plan_context[mapped_key] = value

    return plan_context


def get_owner_plan_context(owner) -> dict[str, Any]:
    billing = _get_owner_billing(owner)
    if not billing:
        return dict(PLAN_CONFIG.get(PlanNames.FREE, {}))

    legacy_code = getattr(billing, "subscription", None)
    plan_version = getattr(billing, "plan_version", None)
    if plan_version is None:
        plan_version = get_plan_version_by_legacy_code(legacy_code)

    if plan_version is None:
        legacy_key = _normalize_code(legacy_code) or PlanNames.FREE
        plan = PLAN_CONFIG.get(legacy_key)
        if not plan:
            return dict(PLAN_CONFIG.get(PlanNames.FREE, {}))
        return dict(plan)

    plan_context = _plan_context_base(plan_version, legacy_code)
    plan_context = _apply_entitlements(plan_context, plan_version)
    return plan_context


def get_plan_context_for_version(plan_version) -> dict[str, Any]:
    legacy_code = getattr(plan_version, "legacy_plan_code", None)
    plan_context = _plan_context_base(plan_version, legacy_code)
    return _apply_entitlements(plan_context, plan_version)
