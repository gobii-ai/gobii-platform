"""Feature gating for the native ContactOut pilot."""

import logging

from django.db import DatabaseError
from waffle import get_waffle_flag_model

from constants.feature_flags import CONTACTOUT_PILOT


logger = logging.getLogger(__name__)


def contactout_enabled_for_agent(agent) -> bool:
    """Return whether an agent owner's user-scoped pilot flag is active."""
    if agent is None or not getattr(agent, "user_id", None):
        return False

    Flag = get_waffle_flag_model()
    try:
        flag = Flag.objects.get(name=CONTACTOUT_PILOT)
    except Flag.DoesNotExist:
        return False
    except (AttributeError, DatabaseError, TypeError, ValueError):
        logger.warning(
            "Unable to load ContactOut pilot flag for agent %s",
            getattr(agent, "id", None),
        )
        return False

    try:
        return bool(flag.is_active_for_user(agent.user))
    except (AttributeError, DatabaseError, TypeError, ValueError):
        logger.warning(
            "Unable to evaluate ContactOut pilot flag for user %s",
            getattr(agent, "user_id", None),
        )
        return False
