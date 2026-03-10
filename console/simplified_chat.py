from dataclasses import dataclass

from django.http import HttpRequest

from api.models import UserPreference
from constants.feature_flags import SIMPLIFIED_CHAT_UI
from waffle import flag_is_active


@dataclass(frozen=True)
class SimplifiedChatState:
    enabled: bool
    toggle_available: bool


def resolve_simplified_chat_state(request: HttpRequest) -> SimplifiedChatState:
    toggle_available = flag_is_active(request, SIMPLIFIED_CHAT_UI)
    enabled = (
        toggle_available
        and request.user.is_authenticated
        and UserPreference.resolve_simplified_chat_enabled(request.user)
    )
    return SimplifiedChatState(enabled=bool(enabled), toggle_available=toggle_available)
