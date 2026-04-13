from django import template

from pages.cta_service import get_cta_current_text

register = template.Library()
_RENDER_CACHE_KEY = "pages.cta_tags.cache"


def _get_render_cache(context) -> dict[str, str | None]:
    render_cache = context.render_context.get(_RENDER_CACHE_KEY)
    if render_cache is None:
        render_cache = {}
        context.render_context[_RENDER_CACHE_KEY] = render_cache
    return render_cache


@register.simple_tag(takes_context=True)
def cta_text(context, slug: str, fallback: str = "") -> str:
    """Resolve the latest CTA text for a slug within a template render."""
    normalized_slug = (slug or "").strip()
    if not normalized_slug:
        return fallback

    render_cache = _get_render_cache(context)
    if normalized_slug not in render_cache:
        render_cache[normalized_slug] = get_cta_current_text(normalized_slug)

    cached_value = render_cache[normalized_slug]
    if cached_value is None:
        return fallback
    return cached_value
