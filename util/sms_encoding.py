"""SMS encoding helpers for avoiding avoidable UCS-2 delivery."""

import math
import unicodedata
from typing import Literal, TypedDict


SmsEncoding = Literal["GSM-7", "UCS-2"]


class SmsOptimizationResult(TypedDict):
    text: str
    changed: bool
    original_encoding: SmsEncoding
    original_segments: int
    normalized_encoding: SmsEncoding
    normalized_segments: int
    final_encoding: SmsEncoding
    final_segments: int
    segments_saved: int


GSM_7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

GSM_7_EXTENDED = frozenset("\f^{}\\[~]|€")

EMOJI_REPLACEMENTS = {
    "😀": ":)",
    "😃": ":)",
    "😄": ":)",
    "😁": ":)",
    "😊": ":)",
    "🙂": ":)",
    "😉": ";)",
    "😅": ":)",
    "😂": ":')",
    "🤣": ":')",
    "😍": "<3",
    "❤️": "<3",
    "❤": "<3",
    "👍": "thumbs up",
    "🙏": "thanks",
    "🔥": "hot",
    "🎉": "congrats",
    "🚀": "launch",
    "✅": "done",
    "❌": "x",
    "⚠️": "warning",
    "⚠": "warning",
}

CHAR_REPLACEMENTS = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "—": "-",
    "–": "-",
    "−": "-",
    "‑": "-",
    "‒": "-",
    "―": "-",
    "…": "...",
    "•": "-",
    "·": "-",
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "™": "",
    "®": "",
    "©": "",
    "\u00a0": " ",
}

TYPOGRAPHY_REPLACEMENTS = dict(CHAR_REPLACEMENTS)


def sms_encoding(text: str) -> SmsEncoding:
    for char in text:
        if char in GSM_7_BASIC or char in GSM_7_EXTENDED:
            continue
        return "UCS-2"
    return "GSM-7"


def estimate_sms_segments(text: str) -> int:
    if not text:
        return 0

    encoding = sms_encoding(text)
    if encoding == "GSM-7":
        septets = sum(2 if char in GSM_7_EXTENDED else 1 for char in text)
        if septets <= 160:
            return 1
        return math.ceil(septets / 153)

    code_units = len(text.encode("utf-16-be", "surrogatepass")) // 2
    if code_units <= 70:
        return 1
    return math.ceil(code_units / 67)


def normalize_sms_text(text: str) -> str:
    for original, replacement in EMOJI_REPLACEMENTS.items():
        text = text.replace(original, replacement)

    for original, replacement in CHAR_REPLACEMENTS.items():
        text = text.replace(original, replacement)

    return _strip_to_gsm7(text)


def optimize_sms_for_cost(text: str, *, max_length: int | None = None) -> SmsOptimizationResult:
    original_encoding = sms_encoding(text)
    original_segments = estimate_sms_segments(text)

    typography_normalized = _normalize_sms_typography(text)
    typography_segments = estimate_sms_segments(typography_normalized)
    typography_within_limit = max_length is None or len(typography_normalized) <= max_length

    normalized = normalize_sms_text(text)
    normalized_encoding = sms_encoding(normalized)
    normalized_segments = estimate_sms_segments(normalized)
    normalized_within_limit = max_length is None or len(normalized) <= max_length

    final_text = text
    final_segments = original_segments
    if (
        typography_within_limit
        and bool(typography_normalized)
        and typography_normalized != text
        and typography_segments <= original_segments
    ):
        final_text = typography_normalized
        final_segments = typography_segments

    if (
        normalized_within_limit
        and bool(normalized)
        and normalized != final_text
        and normalized_segments < final_segments
    ):
        final_text = normalized
        final_segments = normalized_segments

    changed = final_text != text
    final_segments = estimate_sms_segments(final_text)

    return {
        "text": final_text,
        "changed": changed,
        "original_encoding": original_encoding,
        "original_segments": original_segments,
        "normalized_encoding": normalized_encoding,
        "normalized_segments": normalized_segments,
        "final_encoding": sms_encoding(final_text),
        "final_segments": final_segments,
        "segments_saved": original_segments - final_segments,
    }


def _normalize_sms_typography(text: str) -> str:
    for original, replacement in TYPOGRAPHY_REPLACEMENTS.items():
        text = text.replace(original, replacement)

    return text


def _strip_to_gsm7(text: str) -> str:
    result: list[str] = []
    for char in text:
        if char in GSM_7_BASIC or char in GSM_7_EXTENDED:
            result.append(char)
            continue

        normalized = unicodedata.normalize("NFKD", char)
        gsm7_chars = [
            normalized_char
            for normalized_char in normalized
            if normalized_char in GSM_7_BASIC or normalized_char in GSM_7_EXTENDED
        ]
        if gsm7_chars:
            result.extend(gsm7_chars)
        else:
            result.append(char)
    return "".join(result)
