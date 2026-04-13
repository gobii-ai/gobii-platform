from pages.models import CallToActionVersion


def get_cta_current_text(slug: str) -> str | None:
    """Return the latest stored CTA text for a slug, or None when missing."""
    normalized_slug = (slug or "").strip()
    if not normalized_slug:
        return None

    return (
        CallToActionVersion.objects
        .filter(cta__slug=normalized_slug)
        .order_by("-created_at", "-id")
        .values_list("text", flat=True)
        .first()
    )


def get_cta_text(slug: str, fallback: str = "") -> str:
    """Return the latest CTA text, falling back when the slug has no versions."""
    current_text = get_cta_current_text(slug)
    if current_text is None:
        return fallback
    return current_text
