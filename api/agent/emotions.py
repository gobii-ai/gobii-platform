import unicodedata
from datetime import timedelta

import regex
from django.core.exceptions import ValidationError
from django.utils import timezone


MAX_EMOTION_TIMEOUT_SECONDS = 86_400
MAX_EMOTION_LENGTH = 32

_SINGLE_GRAPHEME_RE = regex.compile(r"\A\X\Z")
_PICTOGRAPHIC_RE = regex.compile(r"\p{Extended_Pictographic}")
_EMOJI_PRESENTATION_RE = regex.compile(r"\p{Emoji_Presentation}")
_FLAG_RE = regex.compile(r"\A\p{Regional_Indicator}{2}\Z")
_KEYCAP_RE = regex.compile(r"\A[#*0-9]\uFE0F?\u20E3\Z")


def _is_single_emoji(value: str) -> bool:
    if not _SINGLE_GRAPHEME_RE.fullmatch(value):
        return False
    return bool(
        _PICTOGRAPHIC_RE.search(value)
        or _EMOJI_PRESENTATION_RE.search(value)
        or _FLAG_RE.fullmatch(value)
        or _KEYCAP_RE.fullmatch(value)
    )


def normalize_emotion_update(
    emotion: object,
    timeout_seconds: object,
    *,
    now=None,
):
    """Validate SQLite emotion input and return its persisted value and expiry."""
    if emotion is None and timeout_seconds is None:
        return "", None
    if emotion is None or timeout_seconds is None:
        raise ValidationError(
            {"emotion": "Set emotion and emotion_timeout_seconds together, or clear both with NULL."}
        )
    if not isinstance(emotion, str):
        raise ValidationError({"emotion": "Emotion must be exactly one emoji."})

    normalized = unicodedata.normalize("NFC", emotion.strip())
    if len(normalized) > MAX_EMOTION_LENGTH or not _is_single_emoji(normalized):
        raise ValidationError({"emotion": "Emotion must be exactly one emoji."})
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_EMOTION_TIMEOUT_SECONDS:
        raise ValidationError(
            {
                "emotion_timeout_seconds": (
                    f"Emotion timeout must be an integer from 1 to {MAX_EMOTION_TIMEOUT_SECONDS} seconds."
                )
            }
        )

    current_time = now or timezone.now()
    return normalized, current_time + timedelta(seconds=timeout_seconds)


__all__ = [
    "MAX_EMOTION_LENGTH",
    "MAX_EMOTION_TIMEOUT_SECONDS",
    "normalize_emotion_update",
]
