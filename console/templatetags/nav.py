from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def nav_active(context, *url_names):
    """
    Return Tailwind classes if the current view’s url_name matches any supplied.
    Usage:  class="{{ nav_active 'console-home' 'marketing-home' }}"
    """
    current = context.request.resolver_match.url_name
    return (
        "text-blue-600 bg-blue-50"
        if current in url_names
        else "text-gray-700 hover:bg-gray-100"
    )
