from dataclasses import dataclass

from django.http import HttpRequest

from api.models import UserPreference
from constants.feature_flags import SIMPLIFIED_CHAT_DEFAULT_CONVERSATIONAL, SIMPLIFIED_CHAT_UI
from waffle import flag_is_active


@dataclass(frozen=True)
class SimplifiedChatState:
    enabled: bool
    toggle_available: bool


def resolve_simplified_chat_state(request: HttpRequest) -> SimplifiedChatState:
    toggle_available = flag_is_active(request, SIMPLIFIED_CHAT_UI)
    enabled = False
    if toggle_available and request.user.is_authenticated:
        saved_preference = UserPreference.resolve_optional_simplified_chat_enabled(request.user)
        if saved_preference is None:
            enabled = flag_is_active(request, SIMPLIFIED_CHAT_DEFAULT_CONVERSATIONAL)
        else:
            enabled = saved_preference
    return SimplifiedChatState(enabled=bool(enabled), toggle_available=toggle_available)
