from collections import defaultdict
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import unquote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone

from api.models import (
    TaskCredit,
    UserAttribution,
    UserBilling,
    UserIdentitySignal,
    UserIdentitySignalTypeChoices,
    UserTrialEligibility,
    UserTrialEligibilityAutoStatusChoices,
    UserTrialEligibilityManualActionChoices,
)
from constants.plans import PlanNames
from util.analytics import Analytics
from util.integrations import IntegrationDisabledError
from util.subscription_helper import (
    customer_has_any_individual_subscription,
    get_stripe_customer,
)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore


SIGNUP_FPJS_VISITOR_COOKIE_NAME = "gobii_signup_fpjs_visitor_id"
SIGNUP_FPJS_REQUEST_COOKIE_NAME = "gobii_signup_fpjs_request_id"
SIGNUP_GA_CLIENT_COOKIE_NAME = "gobii_signup_ga_client_id"

SIGNAL_SOURCE_SIGNUP = "signup"
SIGNAL_SOURCE_LOGIN = "login"
SIGNAL_SOURCE_CHECKOUT = "checkout"

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
    try:
        decoded = unquote(raw)
    except Exception:
        decoded = raw
    return decoded.strip().strip('"')


def _safe_client_ip(request) -> str:
    if request is None:
        return ""
    try:
        ip = Analytics.get_client_ip(request)
    except Exception:
        return ""
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


def extract_request_identity_signal_values(request, *, include_fpjs: bool) -> dict[str, str]:
    if request is None:
        return {}

    values: dict[str, str] = {}

    ga_client_id = (
        _decode_value(request.POST.get("uga"))
        or _decode_value(request.COOKIES.get(SIGNUP_GA_CLIENT_COOKIE_NAME))
        or _decode_value(request.COOKIES.get("_ga"))
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
        fpjs_visitor_id = _decode_value(request.POST.get("ufp")) or _decode_value(
            request.COOKIES.get(SIGNUP_FPJS_VISITOR_COOKIE_NAME)
        )
        fpjs_request_id = _decode_value(request.POST.get("ufpr")) or _decode_value(
            request.COOKIES.get(SIGNUP_FPJS_REQUEST_COOKIE_NAME)
        )
        if fpjs_visitor_id:
            values[UserIdentitySignalTypeChoices.FPJS_VISITOR_ID] = fpjs_visitor_id
        if fpjs_request_id:
            values[UserIdentitySignalTypeChoices.FPJS_REQUEST_ID] = fpjs_request_id

    return values


def capture_request_identity_signals(user, request, *, source: str, include_fpjs: bool) -> dict[str, str]:
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


def _filter_users_with_trial_or_subscription_history(user_ids: set[int]) -> set[int]:
    if not user_ids:
        return set()

    UserModel = get_user_model()
    users = UserModel.objects.filter(id__in=user_ids).select_related("billing")
    matched: set[int] = set()

    for candidate in users:
        has_history, _ = _user_has_prior_individual_history(candidate)
        if has_history:
            matched.add(candidate.id)

    return matched


def evaluate_user_trial_eligibility(user, *, request=None, capture_source: str | None = None) -> TrialEligibilityResult:
    if not user or not getattr(user, "pk", None):
        return TrialEligibilityResult(
            eligible=True,
            decision=UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
            reason_codes=[],
            evidence_summary={},
            manual_action=UserTrialEligibilityManualActionChoices.INHERIT,
        )

    if request is not None and capture_source:
        capture_request_identity_signals(
            user,
            request,
            source=capture_source,
            include_fpjs=False,
        )

    reason_codes: list[str] = []
    evidence_summary: dict[str, Any] = {
        "matched_signal_users": {},
        "matched_signal_types": [],
    }

    has_prior_history, history_reason = _user_has_prior_individual_history(user)
    auto_status = UserTrialEligibilityAutoStatusChoices.ELIGIBLE
    if has_prior_history:
        auto_status = UserTrialEligibilityAutoStatusChoices.NO_TRIAL
        reason_codes.append(history_reason or "prior_subscription_history")
    else:
        signal_matches: dict[str, set[int]] = {}
        per_user_signal_types: dict[int, set[str]] = defaultdict(set)

        signal_types = [
            UserIdentitySignalTypeChoices.FPJS_VISITOR_ID,
            UserIdentitySignalTypeChoices.FPJS_REQUEST_ID,
            UserIdentitySignalTypeChoices.FBP,
            UserIdentitySignalTypeChoices.GA_CLIENT_ID,
            UserIdentitySignalTypeChoices.IP_EXACT,
            UserIdentitySignalTypeChoices.IP_PREFIX,
        ]

        for signal_type in signal_types:
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
                str(user_id): sorted(signal_types)
                for user_id, signal_types in per_user_signal_types.items()
            }

        strong_match = bool(
            signal_matches.get(UserIdentitySignalTypeChoices.FPJS_VISITOR_ID)
            or signal_matches.get(UserIdentitySignalTypeChoices.FPJS_REQUEST_ID)
        )
        if strong_match:
            auto_status = UserTrialEligibilityAutoStatusChoices.NO_TRIAL
            reason_codes.append("fpjs_history_match")
        else:
            medium_signal_types = {
                UserIdentitySignalTypeChoices.FBP,
                UserIdentitySignalTypeChoices.GA_CLIENT_ID,
                UserIdentitySignalTypeChoices.IP_EXACT,
                UserIdentitySignalTypeChoices.IP_PREFIX,
            }
            medium_match = any(
                len(signal_types & medium_signal_types) >= 2
                for signal_types in per_user_signal_types.values()
            )
            if medium_match:
                auto_status = UserTrialEligibilityAutoStatusChoices.REVIEW
                reason_codes.append("multi_signal_history_match")

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
    return TrialEligibilityResult(
        eligible=effective_status == UserTrialEligibilityAutoStatusChoices.ELIGIBLE,
        decision=effective_status,
        reason_codes=list(reason_codes),
        evidence_summary=evidence_summary,
        manual_action=eligibility.manual_action,
    )
