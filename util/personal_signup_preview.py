from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest

from constants.feature_flags import (
    PERSONAL_AGENT_SIGNUP_PREVIEW_PROCESSING_LIMIT,
    PERSONAL_AGENT_SIGNUP_PREVIEW_UI,
    PERSONAL_AGENT_SIGNUP_STARTER_CHARTER,
)
from util.onboarding import clear_trial_onboarding_intent, get_trial_onboarding_state
from util.trial_enforcement import can_user_use_personal_agents_and_api
from util.urls import IMMERSIVE_APP_BASE_PATH, append_query_params
from util.waffle_flags import is_waffle_flag_active


GENERIC_STARTER_CHARTER = "Hello!"


SIGNUP_PREVIEW_FIRST_RUN_PROMPT_BLOCK = """
## Signup Preview First-Run Override

This user has not completed signup yet and this run is a limited preview.

- Keep your first message lightweight, useful, and easy to reply to.
- Introduce yourself briefly and confidently.
- Help the user get oriented quickly.
- Ask 2 or 3 concise clarifying questions about the kind of work they want help with.
- Offer a few concrete examples of useful things you can do next.
- Tell the user you can continue once signup is completed.
- Do not overwhelm the user with a long onboarding speech.

## Override For This First Run

- Your first action must still be sending the welcome message.
- For this preview first run, do not call sqlite_batch or any other tool in the same response.
- After sending that first helpful message, stop. Processing will pause until signup is completed.
""".strip()


@dataclass(frozen=True)
class PersonalSignupPreviewConfig:
    current_context_is_personal: bool
    eligible_user: bool
    starter_charter_flag_enabled: bool
    ui_flag_enabled: bool
    processing_limit_flag_enabled: bool

    @property
    def current_context_is_eligible(self) -> bool:
        return self.current_context_is_personal and self.eligible_user

    @property
    def starter_charter_enabled(self) -> bool:
        return self.current_context_is_eligible and self.starter_charter_flag_enabled

    @property
    def ui_enabled(self) -> bool:
        return self.current_context_is_eligible and self.ui_flag_enabled

    @property
    def processing_limit_enabled(self) -> bool:
        return self.current_context_is_eligible and self.processing_limit_flag_enabled

    @property
    def suppresses_legacy_plan_modal(self) -> bool:
        return self.ui_enabled and self.processing_limit_enabled

    def should_synthesize_starter_charter(
        self,
        *,
        saved_charter: str | None,
        pending_onboarding: bool,
    ) -> bool:
        return bool(
            self.starter_charter_enabled
            and not (saved_charter or "").strip()
            and not pending_onboarding
        )


@dataclass(frozen=True)
class PersonalSignupPreviewOnboardingState:
    pending: bool
    target: str | None
    requires_plan_selection: bool


def is_personal_signup_preview_feature_enabled(
    flag_name: str,
    request: HttpRequest | None = None,
) -> bool:
    if not settings.GOBII_PROPRIETARY_MODE:
        return False
    return is_waffle_flag_active(flag_name, request, default=False)


def is_personal_signup_starter_charter_enabled(request: HttpRequest | None = None) -> bool:
    return is_personal_signup_preview_feature_enabled(
        PERSONAL_AGENT_SIGNUP_STARTER_CHARTER,
        request,
    )


def is_personal_signup_preview_ui_enabled(request: HttpRequest | None = None) -> bool:
    return is_personal_signup_preview_feature_enabled(
        PERSONAL_AGENT_SIGNUP_PREVIEW_UI,
        request,
    )


def is_personal_signup_preview_processing_limit_enabled(
    request: HttpRequest | None = None,
) -> bool:
    return is_personal_signup_preview_feature_enabled(
        PERSONAL_AGENT_SIGNUP_PREVIEW_PROCESSING_LIMIT,
        request,
    )


def can_use_personal_signup_preview(user) -> bool:
    if not settings.GOBII_PROPRIETARY_MODE:
        return False
    if not user or not getattr(user, "pk", None):
        return False
    return not can_user_use_personal_agents_and_api(user)


def resolve_personal_signup_preview(
    user,
    *,
    request: HttpRequest | None = None,
    current_context_type: str | None = None,
) -> PersonalSignupPreviewConfig:
    return PersonalSignupPreviewConfig(
        current_context_is_personal=current_context_type == "personal",
        eligible_user=can_use_personal_signup_preview(user),
        starter_charter_flag_enabled=is_personal_signup_starter_charter_enabled(request),
        ui_flag_enabled=is_personal_signup_preview_ui_enabled(request),
        processing_limit_flag_enabled=is_personal_signup_preview_processing_limit_enabled(request),
    )


def build_personal_signup_starter_charter() -> str:
    return GENERIC_STARTER_CHARTER


def resolve_personal_signup_preview_onboarding_state(
    request: HttpRequest,
    *,
    preview_config: PersonalSignupPreviewConfig,
) -> PersonalSignupPreviewOnboardingState:
    pending, target, requires_plan_selection = get_trial_onboarding_state(request)
    if preview_config.suppresses_legacy_plan_modal:
        clear_trial_onboarding_intent(request)
        return PersonalSignupPreviewOnboardingState(
            pending=False,
            target=None,
            requires_plan_selection=False,
        )
    return PersonalSignupPreviewOnboardingState(
        pending=pending,
        target=target,
        requires_plan_selection=requires_plan_selection,
    )


def get_personal_signup_preview_signup_redirect_url(
    request: HttpRequest,
    *,
    user,
) -> str | None:
    preview_config = resolve_personal_signup_preview(
        user,
        request=request,
        current_context_type="personal",
    )
    onboarding_state = resolve_personal_signup_preview_onboarding_state(
        request,
        preview_config=preview_config,
    )
    saved_charter = request.session.get("agent_charter")
    if not preview_config.should_synthesize_starter_charter(
        saved_charter=saved_charter,
        pending_onboarding=onboarding_state.pending,
    ):
        return None
    return append_query_params(
        f"{IMMERSIVE_APP_BASE_PATH}/agents/new",
        {"spawn": "1"},
    )
