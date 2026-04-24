"""Shared helpers for persistent-agent internal reasoning steps."""

INTERNAL_REASONING_PREFIX = "Internal reasoning:"
REASONING_ONLY_MARKER = "[reasoning-only]"
REASONING_ONLY_PREFIX = f"{INTERNAL_REASONING_PREFIX} {REASONING_ONLY_MARKER}"


def is_internal_reasoning_description(description: str | None) -> bool:
    """Return True when the step description encodes internal reasoning."""

    if not description:
        return False
    return description.startswith(INTERNAL_REASONING_PREFIX)


def is_reasoning_only_description(description: str | None) -> bool:
    """Return True when the step encodes a preserved reasoning-only completion."""

    if not is_internal_reasoning_description(description):
        return False
    return description.startswith(REASONING_ONLY_PREFIX)


def build_internal_reasoning_description(
    reasoning_text: str | None,
    *,
    reasoning_only: bool = False,
) -> str:
    """Render persisted internal reasoning text with the canonical prefix."""

    reasoning = (reasoning_text or "").strip()
    prefix = REASONING_ONLY_PREFIX if reasoning_only else INTERNAL_REASONING_PREFIX
    if not reasoning:
        return prefix
    return f"{prefix} {reasoning}"


def strip_internal_reasoning_prefix(description: str | None) -> str:
    """Return the reasoning body without the canonical prefix."""

    if not is_internal_reasoning_description(description):
        return (description or "").strip()

    stripped = description[len(INTERNAL_REASONING_PREFIX):].lstrip()
    if stripped.startswith(REASONING_ONLY_MARKER):
        stripped = stripped[len(REASONING_ONLY_MARKER):].lstrip()
    return stripped
