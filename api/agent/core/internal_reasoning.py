"""Shared helpers for persistent-agent internal reasoning steps."""

INTERNAL_REASONING_PREFIX = "Internal reasoning:"


def is_internal_reasoning_description(description: str | None) -> bool:
    """Return True when the step description encodes internal reasoning."""

    if not description:
        return False
    return description.startswith(INTERNAL_REASONING_PREFIX)


def build_internal_reasoning_description(reasoning_text: str | None) -> str:
    """Render persisted internal reasoning text with the canonical prefix."""

    reasoning = (reasoning_text or "").strip()
    if not reasoning:
        return INTERNAL_REASONING_PREFIX
    return f"{INTERNAL_REASONING_PREFIX} {reasoning}"


def strip_internal_reasoning_prefix(description: str | None) -> str:
    """Return the reasoning body without the canonical prefix."""

    if not is_internal_reasoning_description(description):
        return (description or "").strip()
    return description[len(INTERNAL_REASONING_PREFIX):].lstrip()
