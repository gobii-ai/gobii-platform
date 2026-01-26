import logging

from waffle import get_waffle_flag_model

from constants.feature_flags import SANDBOX_COMPUTE

logger = logging.getLogger(__name__)


def has_sandbox_access(user) -> bool:
    if not user or not getattr(user, "id", None):
        return False

    try:
        flag = get_waffle_flag_model().get(SANDBOX_COMPUTE)
    except Exception:
        logger.exception("Failed loading waffle flag '%s'", SANDBOX_COMPUTE)
        return False

    try:
        return bool(flag.is_active_for_user(user))
    except Exception:
        logger.exception("Failed evaluating waffle flag '%s' for user %s", SANDBOX_COMPUTE, user.id)
        return False
