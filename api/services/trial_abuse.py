from collections import defaultdict
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import unquote

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from api.models import (
    TaskCredit,
    UserAttribution,
    UserFingerprintVisit,
    UserBilling,
    UserIdentitySignal,
    UserIdentitySignalTypeChoices,
    UserTrialEligibility,
    UserTrialEligibilityAutoStatusChoices,
    UserTrialEligibilityManualActionChoices,
)
from api.services.user_fingerprint import stage_user_fingerprint_visit
from constants.plans import PlanNames
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.integrations import IntegrationDisabledError
from util.subscription_helper import (
    _individual_plan_price_ids,
    _individual_plan_product_ids,
    customer_has_any_individual_subscription,
    get_stripe_customer,
)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore

try:
    from djstripe.models import Subscription as DjstripeSubscription
except Exception:  # pragma: no cover - optional dependency
    DjstripeSubscription = None  # type: ignore


SIGNUP_FPJS_VISITOR_COOKIE_NAME = "gobii_signup_fpjs_visitor_id"
SIGNUP_FPJS_REQUEST_COOKIE_NAME = "gobii_signup_fpjs_request_id"
SIGNUP_GA_CLIENT_COOKIE_NAME = "gobii_signup_ga_client_id"

SIGNAL_SOURCE_SIGNUP = "signup"
SIGNAL_SOURCE_LOGIN = "login"
SIGNAL_SOURCE_CHECKOUT = "checkout"

FPJS_REQUEST_ID_MAX_LENGTH = UserFingerprintVisit._meta.get_field("fingerprint_event_id").max_length
FPJS_VISITOR_ID_MAX_LENGTH = UserFingerprintVisit._meta.get_field("fingerprint_visitor_id").max_length

_STRIPE_ELIGIBILITY_ERRORS: tuple[type[BaseException], ...]
if stripe is not None:
    _STRIPE_ELIGIBILITY_ERRORS = (
        IntegrationDisabledError,
        stripe.error.StripeError,
        TypeError,
        ValueError,
    )
else:  # pragma: no cover - stripe is expected in prod, but tests can omit it
    _STRIPE_ELIGIBILITY_ERRORS = (
        IntegrationDisabledError,
        TypeError,
        ValueError,
    )


@dataclass(frozen=True)
class TrialEligibilityResult:
    eligible: bool
    decision: str
    reason_codes: list[str]
    evidence_summary: dict[str, Any]
    manual_action: str


def _decode_value(raw: str | None) -> str:
    if not raw:
        return ""
    decoded = unquote(raw)
    return decoded.strip().strip('"')


def _safe_client_ip(request) -> str:
    if request is None:
        return ""
    ip = Analytics.get_client_ip(request)
    if not ip or ip == "0":
        return ""
    return str(ip).strip()


def _normalize_ip(ip: str) -> str:
    if not ip:
        return ""
    try:
        return ip_address(ip).compressed
    except ValueError:
        return ""


def _normalize_ip_prefix(ip: str) -> str:
    if not ip:
        return ""
    try:
        parsed = ip_address(ip)
    except ValueError:
        return ""
    if parsed.version == 4:
        network = ip_network(f"{parsed}/24", strict=False)
    else:
        network = ip_network(f"{parsed}/56", strict=False)
    return str(network)


def _normalize_ga_client_id(raw: str | None) -> str:
    value = _decode_value(raw)
    if not value:
        return ""

    parts = value.split(".")
    if len(parts) >= 4 and parts[0].upper().startswith("GA"):
        trailing_parts = parts[-2:]
        if all(part.isdigit() for part in trailing_parts):
            return ".".join(trailing_parts)
    return value


def _normalize_fpjs_identifier(raw: str | None, *, max_length: int) -> str:
    value = _decode_value(raw)
    if not value:
        return ""
    # Fingerprint IDs are opaque identifiers; dropping malformed values is safer
    # than truncating into a different identifier that could collide.
    if len(value) > max_length:
        return ""
    return value


def _should_use_staged_fpjs_cookie_fallback(request) -> bool:
    if request is None:
        return False
    return request.method != "POST"


def _medium_signal_family(signal_type: str) -> str | None:
    if signal_type == UserIdentitySignalTypeChoices.FBP:
        return "fbp"
    if signal_type == UserIdentitySignalTypeChoices.GA_CLIENT_ID:
        return "ga_client_id"
    if signal_type in {
        UserIdentitySignalTypeChoices.IP_EXACT,
        UserIdentitySignalTypeChoices.IP_PREFIX,
    }:
        return "ip"
    return None


def extract_request_identity_signal_values(request, *, include_fpjs: bool) -> dict[str, str]:
    if request is None:
        return {}

    values: dict[str, str] = {}

    ga_client_id = (
        _normalize_ga_client_id(request.POST.get("uga"))
        or _normalize_ga_client_id(request.COOKIES.get(SIGNUP_GA_CLIENT_COOKIE_NAME))
        or _normalize_ga_client_id(request.COOKIES.get("_ga"))
    )
    fbp = _decode_value(request.COOKIES.get(settings.FBP_COOKIE_NAME)) or _decode_value(getattr(request, "fbp", ""))

    normalized_ip = _normalize_ip(_safe_client_ip(request))
    ip_prefix = _normalize_ip_prefix(normalized_ip)

    if ga_client_id:
        values[UserIdentitySignalTypeChoices.GA_CLIENT_ID] = ga_client_id
    if fbp:
        values[UserIdentitySignalTypeChoices.FBP] = fbp
    if normalized_ip:
        values[UserIdentitySignalTypeChoices.IP_EXACT] = normalized_ip
    if ip_prefix:
        values[UserIdentitySignalTypeChoices.IP_PREFIX] = ip_prefix

    if include_fpjs:
        fpjs_visitor_id = _normalize_fpjs_identifier(
            request.POST.get("ufp"),
            max_length=FPJS_VISITOR_ID_MAX_LENGTH,
        )
        fpjs_request_id = _normalize_fpjs_identifier(
            request.POST.get("ufpr"),
            max_length=FPJS_REQUEST_ID_MAX_LENGTH,
        )
        if _should_use_staged_fpjs_cookie_fallback(request):
            fpjs_visitor_id = fpjs_visitor_id or _normalize_fpjs_identifier(
                request.COOKIES.get(SIGNUP_FPJS_VISITOR_COOKIE_NAME),
                max_length=FPJS_VISITOR_ID_MAX_LENGTH,
            )
            fpjs_request_id = fpjs_request_id or _normalize_fpjs_identifier(
                request.COOKIES.get(SIGNUP_FPJS_REQUEST_COOKIE_NAME),
                max_length=FPJS_REQUEST_ID_MAX_LENGTH,
            )
        if fpjs_visitor_id:
            values[UserIdentitySignalTypeChoices.FPJS_VISITOR_ID] = fpjs_visitor_id
        if fpjs_request_id:
            values[UserIdentitySignalTypeChoices.FPJS_REQUEST_ID] = fpjs_request_id

    return values


def capture_request_identity_signals_and_attribution(
    user,
    request,
    *,
    source: str,
    include_fpjs: bool,
) -> dict[str, str]:
    if not user or not getattr(user, "pk", None):
        return {}

    signal_values = extract_request_identity_signal_values(request, include_fpjs=include_fpjs)
    if not signal_values:
        return {}

    observed_at = timezone.now()

    for signal_type, signal_value in signal_values.items():
        signal, created = UserIdentitySignal.objects.get_or_create(
            user=user,
            signal_type=signal_type,
            signal_value=signal_value,
            defaults={
                "first_seen_at": observed_at,
                "last_seen_at": observed_at,
                "first_seen_source": source,
                "last_seen_source": source,
                "observation_count": 1,
            },
        )
        if created:
            continue

        UserIdentitySignal.objects.filter(pk=signal.pk).update(
            last_seen_at=observed_at,
            last_seen_source=source,
            observation_count=F("observation_count") + 1,
        )

    attribution_defaults = {
        "last_user_agent": _decode_value(request.META.get("HTTP_USER_AGENT", "")),
    }
    ga_client_id = signal_values.get(UserIdentitySignalTypeChoices.GA_CLIENT_ID)
    if ga_client_id:
        attribution_defaults["ga_client_id"] = ga_client_id
    fbp = signal_values.get(UserIdentitySignalTypeChoices.FBP)
    if fbp:
        attribution_defaults["fbp"] = fbp
    exact_ip = signal_values.get(UserIdentitySignalTypeChoices.IP_EXACT)
    if exact_ip:
        attribution_defaults["last_client_ip"] = exact_ip

    if attribution_defaults:
        UserAttribution.objects.update_or_create(
            user=user,
            defaults=attribution_defaults,
        )

    if include_fpjs:
        stage_user_fingerprint_visit(
            user,
            source=source,
            signal_values=signal_values,
        )

    return signal_values


def _user_has_local_trial_or_paid_history(user) -> bool:
    if TaskCredit.objects.filter(user=user, free_trial_start=True).exists():
        return True

    billing = getattr(user, "billing", None)
    if billing is None:
        billing = UserBilling.objects.filter(user=user).only("subscription").first()

    return bool(billing and getattr(billing, "subscription", "") != PlanNames.FREE)


def _user_has_prior_individual_history(user) -> tuple[bool, str | None]:
    if _user_has_local_trial_or_paid_history(user):
        return True, "local_billing_or_trial_history"

    customer = get_stripe_customer(user)
    if not customer or not getattr(customer, "id", None):
        return False, None

    try:
        return customer_has_any_individual_subscription(str(customer.id)), None
    except _STRIPE_ELIGIBILITY_ERRORS:
        return True, "subscription_history_lookup_failed"


def user_has_prior_individual_history(user) -> bool:
    has_prior_history, _history_reason = _user_has_prior_individual_history(user)
    return has_prior_history


def _user_ids_with_matching_signal_values(user, signal_type: str) -> set[int]:
    values = list(
        UserIdentitySignal.objects.filter(
            user=user,
            signal_type=signal_type,
        ).values_list("signal_value", flat=True)
    )
    if not values:
        return set()

    return set(
        UserIdentitySignal.objects.filter(
            signal_type=signal_type,
            signal_value__in=values,
        )
        .exclude(user=user)
        .values_list("user_id", flat=True)
    )


def _subscription_data_has_individual_plan(
    stripe_data: Any,
    *,
    plan_products: set[str],
    plan_price_ids: set[str],
) -> bool:
    if not isinstance(stripe_data, dict):
        return False

    items = (stripe_data.get("items") or {}).get("data", []) or []
    for item in items:
        price = item.get("price") or {}
        if not isinstance(price, dict):
            continue

        product = price.get("product")
        if isinstance(product, dict):
            product = product.get("id")

        price_id = price.get("id")
        if (product and str(product) in plan_products) or (price_id and str(price_id) in plan_price_ids):
            return True

    return False


def _user_ids_with_local_individual_subscription_history(user_ids: set[int]) -> set[int]:
    if not user_ids or DjstripeSubscription is None:
        return set()

    plan_products = _individual_plan_product_ids()
    plan_price_ids = _individual_plan_price_ids()
    if not plan_products and not plan_price_ids:
        return set()

    matched: set[int] = set()
    subscription_rows = (
        DjstripeSubscription.objects.filter(
            customer__subscriber_id__in=user_ids,
        )
        .values_list("customer__subscriber_id", "stripe_data")
        .iterator()
    )
    for user_id, stripe_data in subscription_rows:
        if _subscription_data_has_individual_plan(
            stripe_data,
            plan_products=plan_products,
            plan_price_ids=plan_price_ids,
        ):
            matched.add(int(user_id))

    return matched


def _filter_users_with_trial_or_subscription_history(user_ids: set[int]) -> set[int]:
    if not user_ids:
        return set()

    # Signal fanout can be large on shared networks, so candidate history checks
    # stay local to our DB. Only the evaluated user's own history falls back to Stripe.
    matched = set(
        TaskCredit.objects.filter(
            user_id__in=user_ids,
            free_trial_start=True,
        ).values_list("user_id", flat=True)
    )
    matched.update(
        UserBilling.objects.filter(user_id__in=user_ids)
        .exclude(subscription=PlanNames.FREE)
        .values_list("user_id", flat=True)
    )
    matched.update(_user_ids_with_local_individual_subscription_history(user_ids))
    return matched


def _track_trial_eligibility_assessment(
    user,
    *,
    assessment_source: str,
    auto_status: str,
    effective_status: str,
    manual_action: str,
    reason_codes: list[str],
    evidence_summary: dict[str, Any],
) -> None:
    matched_signal_users = evidence_summary.get("matched_signal_users") or {}
    Analytics.track_event(
        user_id=user.id,
        event=AnalyticsEvent.PERSONAL_TRIAL_ELIGIBILITY_ASSESSED,
        source=AnalyticsSource.WEB,
        properties={
            "assessment_source": assessment_source,
            "auto_status": auto_status,
            "decision": effective_status,
            "eligible": effective_status == UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
            "manual_action": manual_action,
            "has_manual_override": manual_action != UserTrialEligibilityManualActionChoices.INHERIT,
            "reason_codes": list(reason_codes),
            "matched_signal_types": list(evidence_summary.get("matched_signal_types") or []),
            "matched_signal_user_count": len(matched_signal_users),
        },
    )


def _evaluate_cross_account_trial_abuse(user) -> tuple[str, list[str], dict[str, Any]]:
    reason_codes: list[str] = []
    evidence_summary: dict[str, Any] = {
        "matched_signal_users": {},
        "matched_signal_types": [],
    }
    auto_status = UserTrialEligibilityAutoStatusChoices.ELIGIBLE

    signal_matches: dict[str, set[int]] = {}
    per_user_signal_types: dict[int, set[str]] = defaultdict(set)

    signal_types_to_check = [
        UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
        UserIdentitySignalTypeChoices.FPJS_REQUEST_ID,
        UserIdentitySignalTypeChoices.FBP,
        UserIdentitySignalTypeChoices.GA_CLIENT_ID,
        UserIdentitySignalTypeChoices.IP_EXACT,
        UserIdentitySignalTypeChoices.IP_PREFIX,
    ]

    for signal_type in signal_types_to_check:
        user_ids = _user_ids_with_matching_signal_values(user, signal_type)
        historical_user_ids = _filter_users_with_trial_or_subscription_history(user_ids)
        if not historical_user_ids:
            continue
        signal_matches[signal_type] = historical_user_ids
        for user_id in historical_user_ids:
            per_user_signal_types[user_id].add(signal_type)

    if signal_matches:
        evidence_summary["matched_signal_types"] = sorted(signal_matches)
        evidence_summary["matched_signal_users"] = {
            str(user_id): sorted(matched_signal_types)
            for user_id, matched_signal_types in per_user_signal_types.items()
        }

    strong_match = bool(
        signal_matches.get(UserIdentitySignalTypeChoices.FPJS_VISITOR_ID)
        or signal_matches.get(UserIdentitySignalTypeChoices.FPJS_REQUEST_ID)
    )
    if strong_match:
        auto_status = UserTrialEligibilityAutoStatusChoices.NO_TRIAL
        reason_codes.append("fpjs_history_match")
    else:
        medium_match = any(
            len(
                {
                    signal_family
                    for signal_type in signal_types
                    if (signal_family := _medium_signal_family(signal_type))
                }
            )
            >= 2
            for signal_types in per_user_signal_types.values()
        )
        if medium_match:
            auto_status = UserTrialEligibilityAutoStatusChoices.REVIEW
            reason_codes.append("multi_signal_history_match")

    return auto_status, reason_codes, evidence_summary


def _apply_manual_trial_action(auto_status: str, manual_action: str) -> str:
    if manual_action == UserTrialEligibilityManualActionChoices.ALLOW_TRIAL:
        return UserTrialEligibilityAutoStatusChoices.ELIGIBLE
    if manual_action == UserTrialEligibilityManualActionChoices.DENY_TRIAL:
        return UserTrialEligibilityAutoStatusChoices.NO_TRIAL
    return auto_status


def evaluate_user_trial_identity_abuse(
    user,
    *,
    request=None,
    capture_source: str | None = None,
) -> TrialEligibilityResult:
    """Evaluate cross-account identity abuse without treating this user's own history as abuse."""
    if not user or not getattr(user, "pk", None):
        return TrialEligibilityResult(
            eligible=True,
            decision=UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
            reason_codes=[],
            evidence_summary={},
            manual_action=UserTrialEligibilityManualActionChoices.INHERIT,
        )

    if request is not None and capture_source:
        capture_request_identity_signals_and_attribution(
            user,
            request,
            source=capture_source,
            include_fpjs=False,
        )

    auto_status, reason_codes, evidence_summary = _evaluate_cross_account_trial_abuse(user)
    eligibility = (
        UserTrialEligibility.objects.filter(user=user)
        .only("manual_action")
        .first()
    )
    manual_action = (
        eligibility.manual_action
        if eligibility is not None
        else UserTrialEligibilityManualActionChoices.INHERIT
    )
    effective_status = _apply_manual_trial_action(auto_status, manual_action)

    return TrialEligibilityResult(
        eligible=effective_status == UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
        decision=effective_status,
        reason_codes=list(reason_codes),
        evidence_summary=evidence_summary,
        manual_action=manual_action,
    )


def evaluate_user_trial_eligibility(
    user,
    *,
    request=None,
    capture_source: str | None = None,
    assessment_source: str | None = None,
) -> TrialEligibilityResult:
    if not user or not getattr(user, "pk", None):
        return TrialEligibilityResult(
            eligible=True,
            decision=UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
            reason_codes=[],
            evidence_summary={},
            manual_action=UserTrialEligibilityManualActionChoices.INHERIT,
        )

    if request is not None and capture_source:
        capture_request_identity_signals_and_attribution(
            user,
            request,
            source=capture_source,
            include_fpjs=False,
        )

    has_prior_history, history_reason = _user_has_prior_individual_history(user)
    auto_status = UserTrialEligibilityAutoStatusChoices.ELIGIBLE
    if has_prior_history:
        auto_status = UserTrialEligibilityAutoStatusChoices.NO_TRIAL
        reason_codes = [history_reason or "prior_subscription_history"]
        evidence_summary = {
            "matched_signal_users": {},
            "matched_signal_types": [],
        }
    else:
        auto_status, reason_codes, evidence_summary = _evaluate_cross_account_trial_abuse(user)

    eligibility, _ = UserTrialEligibility.objects.get_or_create(user=user)
    eligibility.auto_status = auto_status
    eligibility.reason_codes = reason_codes
    eligibility.evidence_summary = evidence_summary
    eligibility.evaluated_at = timezone.now()
    eligibility.save(
        update_fields=[
            "auto_status",
            "reason_codes",
            "evidence_summary",
            "evaluated_at",
            "updated_at",
        ]
    )

    effective_status = eligibility.effective_status
    if assessment_source:
        _track_trial_eligibility_assessment(
            user,
            assessment_source=assessment_source,
            auto_status=auto_status,
            effective_status=effective_status,
            manual_action=eligibility.manual_action,
            reason_codes=reason_codes,
            evidence_summary=evidence_summary,
        )
    return TrialEligibilityResult(
        eligible=effective_status == UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
        decision=effective_status,
        reason_codes=list(reason_codes),
        evidence_summary=evidence_summary,
        manual_action=eligibility.manual_action,
    )
