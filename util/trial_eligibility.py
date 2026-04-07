from django.http import HttpRequest

from api.models import UserTrialEligibilityAutoStatusChoices
from constants.feature_flags import (
    START_TRIAL_CAPI_SEND_NO_TRIAL,
    START_TRIAL_CAPI_SEND_REVIEW,
    USER_TRIAL_ELIGIBILITY_ENFORCEMENT,
    USER_TRIAL_REVIEW_ALLOWS_TRIAL,
    START_TRIAL_CAPI_TRIAL_ELIGIBILITY_ENFORCEMENT,
)
from util.waffle_flags import is_waffle_flag_active


def is_user_trial_eligibility_enforcement_enabled(
    request: HttpRequest | None = None,
) -> bool:
    """Default to enabled when the flag row is missing."""
    return is_waffle_flag_active(
        USER_TRIAL_ELIGIBILITY_ENFORCEMENT,
        request,
        default=True,
    )


def is_user_trial_review_allowed(
    request: HttpRequest | None = None,
) -> bool:
    return is_waffle_flag_active(
        USER_TRIAL_REVIEW_ALLOWS_TRIAL,
        request,
        default=False,
    )


def is_trial_decision_allowed(
    decision: str,
    *,
    request: HttpRequest | None = None,
) -> bool:
    if decision == UserTrialEligibilityAutoStatusChoices.ELIGIBLE:
        return True
    if decision == UserTrialEligibilityAutoStatusChoices.REVIEW:
        return is_user_trial_review_allowed(request)
    return False


def is_start_trial_capi_trial_eligibility_enforcement_enabled(
    request: HttpRequest | None = None,
) -> bool:
    """Default to disabled so StartTrial CAPI behavior only changes after rollout."""
    return is_waffle_flag_active(
        START_TRIAL_CAPI_TRIAL_ELIGIBILITY_ENFORCEMENT,
        request,
        default=False,
    )


def is_start_trial_capi_send_review_enabled(
    request: HttpRequest | None = None,
) -> bool:
    return is_waffle_flag_active(
        START_TRIAL_CAPI_SEND_REVIEW,
        request,
        default=False,
    )


def is_start_trial_capi_send_no_trial_enabled(
    request: HttpRequest | None = None,
) -> bool:
    return is_waffle_flag_active(
        START_TRIAL_CAPI_SEND_NO_TRIAL,
        request,
        default=False,
    )


def is_start_trial_capi_decision_allowed(
    decision: str,
    *,
    request: HttpRequest | None = None,
) -> bool:
    if decision == UserTrialEligibilityAutoStatusChoices.ELIGIBLE:
        return True
    if decision == UserTrialEligibilityAutoStatusChoices.REVIEW:
        return is_start_trial_capi_send_review_enabled(request)
    if decision == UserTrialEligibilityAutoStatusChoices.NO_TRIAL:
        return is_start_trial_capi_send_no_trial_enabled(request)
    return True
