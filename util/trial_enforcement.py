import logging

from django.conf import settings
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

from constants.plans import PlanNames
from util.subscription_helper import get_active_subscription
from waffle import get_waffle_switch_model

logger = logging.getLogger(__name__)


PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE = (
    "Start a free trial to use personal agents or personal API keys."
)
PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH = "personal_free_trial_enforcement"


def is_personal_trial_enforcement_enabled() -> bool:
    # Keep env-var support as a hard override, while allowing fast runtime flips via Waffle.
    env_enabled = bool(settings.PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED)
    if env_enabled:
        return True

    try:
        Switch = get_waffle_switch_model()
        switch = Switch.objects.filter(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH,
        ).only("active").first()
    except (DatabaseError, OperationalError, ProgrammingError):
        logger.exception(
            "Failed loading waffle switch '%s' for personal trial enforcement",
            PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH,
        )
        return env_enabled

    if switch is None:
        return env_enabled
    return bool(switch.active)


def is_user_freemium_grandfathered(user) -> bool:
    if not user or not getattr(user, "pk", None):
        return False

    flags = getattr(user, "flags", None)
    if flags is None:
        from api.models import UserFlags

        flags = UserFlags.get_for_user(user)

    return bool(flags and getattr(flags, "is_freemium_grandfathered", False))


def can_user_use_personal_agents_and_api(user) -> bool:
    if not user or not getattr(user, "pk", None):
        return False

    if bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return True

    if not is_personal_trial_enforcement_enabled():
        return True

    if is_user_freemium_grandfathered(user):
        return True

    cache_attr = "_personal_agents_and_api_access_allowed"
    cached = getattr(user, cache_attr, None)
    if cached is not None:
        return bool(cached)

    allowed = False
    try:
        allowed = get_active_subscription(user) is not None
    except Exception:
        logger.exception(
            "Failed to resolve active personal subscription for user %s while enforcing free-trial access",
            getattr(user, "id", None),
        )
        billing = getattr(user, "billing", None)
        allowed = bool(billing and getattr(billing, "subscription", None) != PlanNames.FREE)

    setattr(user, cache_attr, bool(allowed))
    return bool(allowed)
