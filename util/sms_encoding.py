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
    "😂": "haha",
    "🤣": "haha",
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
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
}


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

    text = _strip_to_gsm7(text)

    if "\n" not in text:
        return " ".join(text.split())
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def optimize_sms_for_cost(text: str) -> SmsOptimizationResult:
    original_encoding = sms_encoding(text)
    original_segments = estimate_sms_segments(text)

    normalized = normalize_sms_text(text)
    normalized_encoding = sms_encoding(normalized)
    normalized_segments = estimate_sms_segments(normalized)

    should_use_normalized = bool(normalized) and normalized != text and (
        normalized_segments < original_segments
        or (
            original_encoding == "UCS-2"
            and normalized_encoding == "GSM-7"
            and normalized_segments <= original_segments
        )
    )

    final_text = normalized if should_use_normalized else text
    final_segments = estimate_sms_segments(final_text)

    return {
        "text": final_text,
        "changed": should_use_normalized,
        "original_encoding": original_encoding,
        "original_segments": original_segments,
        "normalized_encoding": normalized_encoding,
        "normalized_segments": normalized_segments,
        "final_encoding": sms_encoding(final_text),
        "final_segments": final_segments,
        "segments_saved": original_segments - final_segments,
    }


def _strip_to_gsm7(text: str) -> str:
    result: list[str] = []
    for char in text:
        if char in GSM_7_BASIC or char in GSM_7_EXTENDED:
            result.append(char)
            continue

        normalized = unicodedata.normalize("NFKD", char)
        result.extend(
            normalized_char
            for normalized_char in normalized
            if normalized_char in GSM_7_BASIC or normalized_char in GSM_7_EXTENDED
        )
    return "".join(result)
