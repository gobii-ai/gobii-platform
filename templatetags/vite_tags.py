from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from config.vite import ViteManifestError, get_vite_asset

register = template.Library()


@register.simple_tag
def vite_asset(entry: str | None = None) -> str:
    try:
        asset = get_vite_asset(entry)
    except ViteManifestError as error:
        return mark_safe(f"<!-- Vite asset error: {error} -->")

    tags: list[str] = []

    for href in asset.styles:
        tags.append(format_html('<link rel="stylesheet" href="{}" />', href))

    scripts = list(asset.scripts)
    if scripts:
        tags.append(format_html('<script type="module" src="{}"></script>', scripts[0]))

    for module_script in asset.inline_modules:
        tags.append(format_html('<script type="module">{}</script>', mark_safe(module_script)))

    for src in scripts[1:]:
        tags.append(format_html('<script type="module" src="{}"></script>', src))

    return mark_safe('\n'.join(tags))
