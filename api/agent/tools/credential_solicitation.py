"""Detect attempts to collect credential values through ordinary human input."""

import re
from typing import Any


_CREDENTIAL_VALUE_PATTERN = (
    r"(?:api\s*[-_ ]?keys?|passwords?|passcodes?|"
    r"(?:access|auth(?:entication)?|bearer|refresh|session)\s+tokens?|tokens?|"
    r"client\s+secrets?|secret\s+keys?|private\s+keys?|secrets?|"
    r"(?:mfa|2fa|otp|one[- ]time|verification|recovery)\s+codes?|"
    r"login\s+credentials?(?!\s+(?:request|link|form|page|flow))|"
    r"credentials?(?!\s+(?:request|link|form|page|flow)))"
)
_POSSESSED_CREDENTIAL_VALUE_PATTERN = (
    rf"(?:your|the|a(?:n)?)\s+(?:[\w.-]+\s+){{0,4}}{_CREDENTIAL_VALUE_PATTERN}"
)
_DIRECT_CREDENTIAL_SOLICITATION_PATTERNS = (
    re.compile(
        rf"(?:^|[.!?\n]\s*)(?:please\s+)?"
        rf"(?:paste|enter|type|submit|upload|provide|share|give|send|email|text|forward)\b"
        rf"[^.!?\n]{{0,80}}\b{_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
        rf"(?:paste|enter|type|submit|upload|provide|share|give|send|email|text|forward)\b"
        rf"[^.!?\n]{{0,80}}\b{_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:paste|enter|type|submit|upload|provide|share|give)\b"
        rf"[^.!?\n]{{0,80}}\b{_POSSESSED_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:send|email|text|forward)\s+(?:me|us)\b"
        rf"[^.!?\n]{{0,80}}\b{_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:reply|respond)\b[^.!?\n]{{0,40}}\bwith\b"
        rf"[^.!?\n]{{0,80}}\b{_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:what\s+is|what's|tell\s+me)\b"
        rf"[^.!?\n]{{0,40}}\b{_POSSESSED_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bi\s+need\b[^.!?\n]{{0,80}}\b{_POSSESSED_CREDENTIAL_VALUE_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_POSSESSED_CREDENTIAL_VALUE_PATTERN}\b"
        rf"[^.!?\n]{{0,80}}\b(?:paste|enter|type|submit|upload|provide|share|send|email|text|reply)\b",
        re.IGNORECASE,
    ),
)
_NEGATED_CREDENTIAL_TRANSFER_PATTERN = re.compile(
    rf"\b(?:do\s+not|don't|never|avoid|must\s+not|should\s+not|cannot|can't)\b"
    rf"[^.!?\n]{{0,40}}\b(?:paste|enter|type|submit|upload|provide|share|give|send|email|text|forward|reply|respond)\b"
    rf"[^.!?\n]{{0,100}}\b{_CREDENTIAL_VALUE_PATTERN}\b",
    re.IGNORECASE,
)
CREDENTIAL_SOLICITATION_ERROR_MESSAGE = (
    "Credential values must never be requested through request_human_input or ordinary messages. "
    "Call secure_credentials_request so the user can provide them through the secure credential flow."
)


def request_solicits_credential_value(question: str, options: list[dict[str, Any]] | None) -> bool:
    option_text = "\n".join(
        f"{option.get('title', '')} {option.get('description', '')}"
        for option in options or []
    )
    text = "\n".join(part for part in (question, option_text) if part).strip()
    if not text:
        return False

    # A warning such as "never paste an API key here" is safe unless another
    # clause independently asks the user to transfer the value.
    text_without_warnings = _NEGATED_CREDENTIAL_TRANSFER_PATTERN.sub("", text)
    return any(pattern.search(text_without_warnings) for pattern in _DIRECT_CREDENTIAL_SOLICITATION_PATTERNS)
