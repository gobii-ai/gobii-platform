import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError

from billing.plan_resolver import get_owner_plan_context
from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames
from util.subscription_helper import get_customer_subscription_candidate, get_stripe_customer


logger = logging.getLogger(__name__)

EVENT_SCHEMA_VERSION = 2
UNKNOWN_PLAN = "unknown"
PERSONAL_ACCOUNT_PREFIX = "user:"

_request_billing_context_cache: ContextVar[
    dict[tuple[str, tuple[str, str]], "AnalyticsBillingContext"] | None
] = ContextVar("request_billing_context_cache", default=None)


class AnalyticsAccessType(StrEnum):
    PAID = "paid"
    TRIAL = "trial"
    GRANDFATHERED_FREE = "grandfathered_free"
    INTERNAL = "internal"
    NONE = "none"
    UNKNOWN = "unknown"


class AnalyticsBillingStatus(StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"
    NONE = "none"
    UNKNOWN = "unknown"


KNOWN_BILLING_STATUSES = frozenset(status.value for status in AnalyticsBillingStatus)
INACTIVE_BILLING_STATUSES = frozenset(
    {
        AnalyticsBillingStatus.PAST_DUE,
        AnalyticsBillingStatus.CANCELED,
        AnalyticsBillingStatus.UNPAID,
        AnalyticsBillingStatus.INCOMPLETE,
        AnalyticsBillingStatus.INCOMPLETE_EXPIRED,
        AnalyticsBillingStatus.PAUSED,
    }
)

# These values are not needed for product analytics. Removing them at the
# centralized boundary prevents message/contact PII from reaching Mixpanel even
# when an older call site still supplies it.
SENSITIVE_EVENT_PROPERTY_NAMES = frozenset(
    {
        "body",
        "endpoint_address",
        "from_address",
        "from_email",
        "from_number",
        "message_body",
        "phone_number",
        "recipient",
        "recipient_email",
        "subject",
        "to_address",
        "to_email",
        "to_number",
        "user_email",
    }
)


@dataclass(frozen=True)
class AnalyticsBillingContext:
    organization_id: str
    plan_at_event: str
    access_type_at_event: str
    billing_status_at_event: str
    is_internal: bool
    event_schema_version: int = EVENT_SCHEMA_VERSION

    def as_event_properties(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "plan_at_event": str(self.plan_at_event),
            "access_type_at_event": str(self.access_type_at_event),
            "billing_status_at_event": str(self.billing_status_at_event),
            "is_internal": self.is_internal,
            "event_schema_version": self.event_schema_version,
        }

    def as_profile_traits(self) -> dict[str, Any]:
        return {
            "current_plan": str(self.plan_at_event),
            "current_access_type": str(self.access_type_at_event),
            "current_billing_status": str(self.billing_status_at_event),
            "is_internal": self.is_internal,
            "is_grandfathered_free": (
                self.access_type_at_event == AnalyticsAccessType.GRANDFATHERED_FREE
            ),
            # Keep the established profile keys during the dashboard migration.
            "plan": str(self.plan_at_event),
            "is_trial": self.access_type_at_event == AnalyticsAccessType.TRIAL,
        }


def bind_request_billing_context_cache() -> Token:
    return _request_billing_context_cache.set({})


def reset_request_billing_context_cache(token: Token) -> None:
    _request_billing_context_cache.reset(token)


def _billing_context_cache_key(
    user_id: object,
    *,
    actor_user: object | None,
    billing_owner: object | None,
    organization_id: object | None,
) -> tuple[str, tuple[str, str]]:
    actor_id = getattr(actor_user, "pk", None) or user_id
    if billing_owner is not None:
        owner_id = getattr(billing_owner, "pk", None)
        owner_type = "personal" if isinstance(billing_owner, get_user_model()) else "organization"
        owner_key = (owner_type, str(owner_id if owner_id is not None else id(billing_owner)))
    elif organization_id:
        organization_id_string = str(organization_id)
        owner_key = (
            ("personal", str(actor_id))
            if organization_id_string.startswith(PERSONAL_ACCOUNT_PREFIX)
            else ("organization", organization_id_string)
        )
    else:
        owner_key = ("personal", str(actor_id))
    return str(actor_id), owner_key


def _is_sensitive_property_name(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_").replace(".", "_")
    return normalized in SENSITIVE_EVENT_PROPERTY_NAMES or normalized == "email" or normalized.endswith("_email")


def sanitize_analytics_event_properties(properties: Mapping[str, Any] | None) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in dict(properties or {}).items():
        if _is_sensitive_property_name(key):
            continue
        if isinstance(value, Mapping):
            sanitized[key] = sanitize_analytics_event_properties(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_analytics_event_properties(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def _get_related_or_none(instance: object, relation_name: str):
    try:
        return getattr(instance, relation_name)
    except ObjectDoesNotExist:
        return None


def _load_actor_user(user_id: object):
    if user_id in (None, ""):
        return None

    User = get_user_model()
    try:
        return (
            User.objects.select_related(
                "billing",
                "billing__plan_version",
                "billing__plan_version__plan",
                "flags",
            )
            .filter(pk=user_id)
            .first()
        )
    except (TypeError, ValidationError, ValueError):
        return None


def _load_organization(organization_id: object):
    if not organization_id:
        return None

    from api.models import Organization

    try:
        return (
            Organization.objects.select_related(
                "billing",
                "billing__plan_version",
                "billing__plan_version__plan",
            )
            .filter(pk=organization_id)
            .first()
        )
    except (TypeError, ValidationError, ValueError):
        return None


def _is_internal_user(user: object | None) -> bool:
    if user is None:
        return False
    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True

    email = str(getattr(user, "email", "") or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return bool(domain and domain in settings.ANALYTICS_INTERNAL_EMAIL_DOMAINS)


def _is_grandfathered_user(user: object | None) -> bool:
    flags = _get_related_or_none(user, "flags") if user is not None else None
    return bool(flags and getattr(flags, "is_freemium_grandfathered", False))


def _normalize_plan(owner: object, billing: object | None) -> str:
    if billing is None:
        return UNKNOWN_PLAN

    try:
        plan_context = get_owner_plan_context(owner)
    except (DatabaseError, LookupError, ObjectDoesNotExist):
        logger.warning(
            "Failed to resolve analytics plan for owner %s",
            getattr(owner, "pk", None),
            exc_info=True,
        )
        return UNKNOWN_PLAN

    raw_plan = str((plan_context or {}).get("slug") or (plan_context or {}).get("id") or "").strip().lower()
    normalized = PLAN_SLUG_BY_LEGACY_CODE.get(raw_plan, raw_plan)
    return normalized or UNKNOWN_PLAN


def _normalize_billing_status(raw_status: object) -> str:
    status = str(raw_status or "").strip().lower()
    if not status:
        return AnalyticsBillingStatus.UNKNOWN
    if status == "cancelled":
        status = AnalyticsBillingStatus.CANCELED
    if status in KNOWN_BILLING_STATUSES:
        return status
    return AnalyticsBillingStatus.UNKNOWN


def _resolve_billing_status(owner: object, billing: object | None, plan: str) -> str:
    if billing is None:
        return AnalyticsBillingStatus.UNKNOWN

    try:
        customer = get_stripe_customer(owner)
    except (DatabaseError, ObjectDoesNotExist, TypeError):
        logger.warning(
            "Failed to resolve Stripe customer for analytics owner %s",
            getattr(owner, "pk", None),
            exc_info=True,
        )
        return AnalyticsBillingStatus.UNKNOWN
    if customer is None:
        return AnalyticsBillingStatus.NONE if plan == PlanNames.FREE else AnalyticsBillingStatus.UNKNOWN

    try:
        subscriptions = list(customer.subscriptions.all())
    except (AttributeError, TypeError, DatabaseError):
        logger.warning(
            "Failed to inspect subscriptions for analytics owner %s",
            getattr(owner, "pk", None),
            exc_info=True,
        )
        return AnalyticsBillingStatus.UNKNOWN

    if not subscriptions:
        return AnalyticsBillingStatus.NONE if plan == PlanNames.FREE else AnalyticsBillingStatus.UNKNOWN

    candidate = get_customer_subscription_candidate(owner, subscriptions)
    if candidate is None:
        return AnalyticsBillingStatus.UNKNOWN

    stripe_data = getattr(candidate, "stripe_data", None)
    raw_status = stripe_data.get("status") if isinstance(stripe_data, Mapping) else None
    if not raw_status:
        raw_status = getattr(candidate, "status", None)
    return _normalize_billing_status(raw_status)


def _classify_access(
    *,
    plan: str,
    billing_status: str,
    is_internal: bool,
    is_grandfathered: bool,
    billing_exists: bool,
) -> str:
    if is_internal:
        return AnalyticsAccessType.INTERNAL
    if not billing_exists or plan == UNKNOWN_PLAN:
        return AnalyticsAccessType.UNKNOWN
    if plan == PlanNames.FREE:
        if is_grandfathered:
            return AnalyticsAccessType.GRANDFATHERED_FREE
        if billing_status == AnalyticsBillingStatus.NONE or billing_status in INACTIVE_BILLING_STATUSES:
            return AnalyticsAccessType.NONE
        return AnalyticsAccessType.UNKNOWN
    if billing_status == AnalyticsBillingStatus.TRIALING:
        return AnalyticsAccessType.TRIAL
    if billing_status == AnalyticsBillingStatus.ACTIVE:
        return AnalyticsAccessType.PAID
    if billing_status in INACTIVE_BILLING_STATUSES:
        return AnalyticsAccessType.NONE
    return AnalyticsAccessType.UNKNOWN


def unknown_billing_context(
    user_id: object,
    *,
    actor_user: object | None = None,
    billing_owner: object | None = None,
    organization_id: object | None = None,
) -> AnalyticsBillingContext:
    if isinstance(billing_owner, get_user_model()):
        account_id = f"{PERSONAL_ACCOUNT_PREFIX}{billing_owner.pk}"
    elif billing_owner is not None:
        account_id = str(getattr(billing_owner, "pk", "") or "unknown")
    elif organization_id:
        account_id = str(organization_id)
    else:
        account_id = f"{PERSONAL_ACCOUNT_PREFIX}{user_id}"
    is_internal = _is_internal_user(actor_user)
    return AnalyticsBillingContext(
        organization_id=account_id,
        plan_at_event=UNKNOWN_PLAN,
        access_type_at_event=(AnalyticsAccessType.INTERNAL if is_internal else AnalyticsAccessType.UNKNOWN),
        billing_status_at_event=AnalyticsBillingStatus.UNKNOWN,
        is_internal=is_internal,
    )


def build_current_billing_profile_traits(
    user: object,
    *,
    plan: object,
    billing_status: object,
) -> dict[str, Any]:
    """Build mutable personal-profile traits from an authoritative lifecycle update."""
    raw_plan = str(plan or "").strip().lower()
    normalized_plan = PLAN_SLUG_BY_LEGACY_CODE.get(raw_plan, raw_plan) or UNKNOWN_PLAN
    normalized_status = _normalize_billing_status(billing_status)
    is_internal = _is_internal_user(user)
    access_type = _classify_access(
        plan=normalized_plan,
        billing_status=normalized_status,
        is_internal=is_internal,
        is_grandfathered=_is_grandfathered_user(user),
        billing_exists=True,
    )

    context = AnalyticsBillingContext(
        organization_id=f"{PERSONAL_ACCOUNT_PREFIX}{getattr(user, 'pk', '')}",
        plan_at_event=normalized_plan,
        access_type_at_event=access_type,
        billing_status_at_event=normalized_status,
        is_internal=is_internal,
    )
    return context.as_profile_traits()


def resolve_analytics_billing_context(
    user_id: object,
    *,
    actor_user: object | None = None,
    billing_owner: object | None = None,
    organization_id: object | None = None,
) -> AnalyticsBillingContext:
    actor = actor_user or _load_actor_user(user_id)
    owner = billing_owner

    if owner is None and organization_id:
        organization_id_string = str(organization_id)
        if not organization_id_string.startswith(PERSONAL_ACCOUNT_PREFIX):
            owner = _load_organization(organization_id_string)
            if owner is None:
                return unknown_billing_context(
                    user_id,
                    actor_user=actor,
                    organization_id=organization_id_string,
                )
    if owner is None:
        owner = actor
    if owner is None:
        return unknown_billing_context(user_id, actor_user=actor)

    owner_is_user = isinstance(owner, get_user_model())
    account_id = f"{PERSONAL_ACCOUNT_PREFIX}{owner.pk}" if owner_is_user else str(owner.pk)
    billing = _get_related_or_none(owner, "billing")
    plan = _normalize_plan(owner, billing)
    billing_status = _resolve_billing_status(owner, billing, plan)
    is_internal = _is_internal_user(actor)
    access_type = _classify_access(
        plan=plan,
        billing_status=billing_status,
        is_internal=is_internal,
        is_grandfathered=owner_is_user and _is_grandfathered_user(owner),
        billing_exists=billing is not None,
    )

    return AnalyticsBillingContext(
        organization_id=account_id,
        plan_at_event=plan,
        access_type_at_event=access_type,
        billing_status_at_event=billing_status,
        is_internal=is_internal,
    )


def resolve_analytics_billing_context_safely(
    user_id: object,
    *,
    actor_user: object | None = None,
    billing_owner: object | None = None,
    organization_id: object | None = None,
    use_request_cache: bool = True,
) -> AnalyticsBillingContext:
    cache = _request_billing_context_cache.get() if use_request_cache else None
    cache_key = _billing_context_cache_key(
        user_id,
        actor_user=actor_user,
        billing_owner=billing_owner,
        organization_id=organization_id,
    )
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    try:
        context = resolve_analytics_billing_context(
            user_id,
            actor_user=actor_user,
            billing_owner=billing_owner,
            organization_id=organization_id,
        )
    except Exception:
        # This boundary is intentionally broad: analytics must not interrupt the
        # product action or page load that triggered enrichment.
        logger.exception("Failed to resolve analytics billing context for user %s", user_id)
        context = unknown_billing_context(
            user_id,
            actor_user=actor_user,
            billing_owner=billing_owner,
            organization_id=organization_id,
        )
    if cache is not None:
        cache[cache_key] = context
    return context
