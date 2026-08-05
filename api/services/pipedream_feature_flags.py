from constants.feature_flags import PIPEDREAM_GOOGLE_SHEETS_GUARD
from util.waffle_flags import is_waffle_switch_active


def pipedream_google_sheets_guard_enabled() -> bool:
    return is_waffle_switch_active(PIPEDREAM_GOOGLE_SHEETS_GUARD, default=True)
