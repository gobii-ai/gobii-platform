TRIAL_ONBOARDING_PENDING_SESSION_KEY = "trial_onboarding_pending"
TRIAL_ONBOARDING_TARGET_SESSION_KEY = "trial_onboarding_target"
TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY = "trial_onboarding_requires_plan_selection"

TRIAL_ONBOARDING_TARGET_AGENT_UI = "agent_ui"
TRIAL_ONBOARDING_TARGET_API_KEYS = "api_keys"
_TRIAL_ONBOARDING_ALLOWED_TARGETS = {
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_API_KEYS,
}


def is_truthy_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_trial_onboarding_target(value: str | None, *, default: str) -> str:
    candidate = (value or "").strip().lower()
    if candidate in _TRIAL_ONBOARDING_ALLOWED_TARGETS:
        return candidate
    return default


def set_trial_onboarding_intent(request, *, target: str) -> None:
    request.session[TRIAL_ONBOARDING_PENDING_SESSION_KEY] = True
    request.session[TRIAL_ONBOARDING_TARGET_SESSION_KEY] = normalize_trial_onboarding_target(
        target,
        default=TRIAL_ONBOARDING_TARGET_AGENT_UI,
    )
    request.session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = False
    request.session.modified = True


def set_trial_onboarding_requires_plan_selection(request, *, required: bool) -> None:
    if not request.session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY):
        return
    request.session[TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY] = bool(required)
    request.session.modified = True


def clear_trial_onboarding_intent(request) -> None:
    changed = False
    for key in (
        TRIAL_ONBOARDING_PENDING_SESSION_KEY,
        TRIAL_ONBOARDING_TARGET_SESSION_KEY,
        TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    ):
        if key in request.session:
            del request.session[key]
            changed = True
    if changed:
        request.session.modified = True


def get_trial_onboarding_state(request) -> tuple[bool, str | None, bool]:
    pending = bool(request.session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
    target = None
    requires_plan_selection = False
    if pending:
        target = normalize_trial_onboarding_target(
            request.session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            default=TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        requires_plan_selection = bool(
            request.session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY)
        )
    return pending, target, requires_plan_selection
