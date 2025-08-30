from django import template
import re
from util.analytics import PAGE_META

register = template.Library()

@register.simple_tag(takes_context=True)
def get_page_meta(context, path):
    for pattern, meta in PAGE_META.items():
        if re.match(pattern, path):
            return meta
    return None