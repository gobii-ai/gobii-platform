from django.http import HttpRequest

from api.models import UserTrialEligibilityAutoStatusChoices
from constants.feature_flags import (
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
