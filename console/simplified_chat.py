from dataclasses import dataclass

from django.http import HttpRequest

from constants.feature_flags import SIMPLIFIED_CHAT_UI
from waffle import flag_is_active


@dataclass(frozen=True)
class SimplifiedChatState:
    enabled: bool
    toggle_available: bool


def resolve_simplified_chat_state(request: HttpRequest) -> SimplifiedChatState:
    enabled = flag_is_active(request, SIMPLIFIED_CHAT_UI)
    return SimplifiedChatState(enabled=bool(enabled), toggle_available=False)
