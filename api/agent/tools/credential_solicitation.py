"""Detect attempts to collect credential values through ordinary human input."""

import re
from typing import Any


_CREDENTIAL = (
    r"(?:api\s*[-_ ]?keys?|passwords?|passcodes?|"
    r"(?:access|auth(?:entication)?|bearer|refresh|session)\s+tokens?|tokens?|"
    r"client\s+secrets?|secret\s+keys?|private\s+keys?|secrets?|"
    r"(?:mfa|2fa|otp|one[- ]time|verification|recovery)\s+codes?|"
    r"login\s+credentials?|credentials?)"
)
_ACTION = r"(?:paste|enter|type|submit|upload|provide|share|give|supply|send|email|text|forward|put|drop|reply|respond|tell)"
_SOLICITATION_RE = re.compile(
    rf"\b(?:paste|enter|type|submit|upload|send|email|text|forward|put|drop)\b|"
    rf"\b(?:reply|respond)\b.{{0,20}}\bwith\b|"
    rf"\b(?:what\s+is|what's|tell\s+me|i\s+need|(?:can|could|may)\s+i\s+have)\b|"
    rf"\b(?:provide|share|give|supply)\b(?:\s+(?:me|us))?\s+"
    rf"(?:{_CREDENTIAL}|(?:your|the|a(?:n)?)\s+(?:[\w.-]+\s+){{0,4}}{_CREDENTIAL})\b",
    re.IGNORECASE,
)
_SANITIZERS = (
    re.compile(
        rf"\b(?:do\s+not|don't|never|avoid|must\s+not|should\s+not|cannot|can't|can\s+you\s+not)\b"
        rf"[^,;.!?]{{0,40}}?\b{_ACTION}\b[^,;.!?]{{0,100}}?\b{_CREDENTIAL}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:(?:secure\s+)?credential\s+|secure\s+)(?:request|link|form|page|flow)\b"
        rf"(?:[^,;.!?]{{0,40}}\b(?:for|to\s+collect)\b[^,;.!?]{{0,30}}\b{_CREDENTIAL}\b)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_CREDENTIAL}\s+(?:name|label|id|format|policy|rotation|expiration|expiry|status)\b|"
        rf"\b(?:documentation|docs|guidance|policy|instructions|details)\b.{{0,30}}"
        rf"\b(?:about|for|on|regarding|of)\b.{{0,30}}\b{_CREDENTIAL}\b",
        re.IGNORECASE,
    ),
)
_CREDENTIAL_RE = re.compile(rf"\b{_CREDENTIAL}\b", re.IGNORECASE)
CREDENTIAL_SOLICITATION_ERROR_MESSAGE = (
    "Credential values must never be requested through request_human_input or ordinary messages. "
    "Call secure_credentials_request so the user can provide them through the secure credential flow."
)


def request_solicits_credential_value(question: str, options: list[dict[str, Any]] | None) -> bool:
    option_text = "\n".join(
        f"{option.get('title', '')} {option.get('description', '')}" for option in options or []
    )
    clauses = re.split(r"[.!?;\n]+|\b(?:but|instead|then)\b", f"{question}\n{option_text}", flags=re.IGNORECASE)
    for clause in clauses:
        for sanitizer in _SANITIZERS:
            clause = sanitizer.sub("", clause)
        if _CREDENTIAL_RE.search(clause) and _SOLICITATION_RE.search(clause):
            return True
    return False
