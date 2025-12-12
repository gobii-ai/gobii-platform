from urllib.parse import quote

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def auth_redirect_url(context, view_name, redirect_value=None, redirect_field_name=None):
    """
    Build a URL to the given auth view while preserving next and stored UTM params.
    """
    request = context.get("request")
    base_url = reverse(view_name)

    redirect_param = redirect_field_name or context.get("redirect_field_name") or "next"
    query_parts: list[str] = []

    if redirect_value:
        encoded_redirect = quote(str(redirect_value), safe="/")
        query_parts.append(f"{redirect_param}={encoded_redirect}")

    if request:
        utm_qs = (request.session.get("utm_querystring") or "").lstrip("?")
        if utm_qs:
            query_parts.append(utm_qs)

    if not query_parts:
        return mark_safe(base_url)

    separator = "&" if "?" in base_url else "?"
    return mark_safe(f"{base_url}{separator}{'&'.join(query_parts)}")
