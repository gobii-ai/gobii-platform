import json
import re
from functools import lru_cache

from django import template
from django.conf import settings
from django.forms.utils import flatatt
from django.utils.html import format_html
from django.utils.safestring import mark_safe


register = template.Library()

_ICON_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ICON_ELEMENTS = frozenset({"circle", "ellipse", "line", "path", "polygon", "polyline", "rect"})
_ICON_ATTRIBUTES = frozenset(
    {
        "cx",
        "cy",
        "d",
        "fill",
        "height",
        "points",
        "r",
        "rx",
        "ry",
        "width",
        "x",
        "x1",
        "x2",
        "y",
        "y1",
        "y2",
    }
)


@lru_cache(maxsize=1)
def _load_icon_catalog() -> dict[str, list[list[object]]]:
    catalog_path = settings.BASE_DIR / "vendor" / "lucide" / "icons.json"
    with catalog_path.open(encoding="utf-8") as catalog_file:
        catalog = json.load(catalog_file)
    return catalog["icons"]


@lru_cache(maxsize=256)
def _render_icon_nodes(icon_name: str) -> str:
    try:
        icon_nodes = _load_icon_catalog()[icon_name]
    except KeyError as error:
        raise template.TemplateSyntaxError(
            f"Unknown Lucide icon {icon_name!r}. Check the name at https://lucide.dev/icons/."
        ) from error

    rendered_nodes: list[str] = []
    for element_name, attributes in icon_nodes:
        if element_name not in _ICON_ELEMENTS or not isinstance(attributes, dict):
            raise template.TemplateSyntaxError(
                f"Invalid catalog entry for Lucide icon {icon_name!r}."
            )
        if not set(attributes).issubset(_ICON_ATTRIBUTES):
            raise template.TemplateSyntaxError(
                f"Invalid catalog attributes for Lucide icon {icon_name!r}."
            )
        rendered_nodes.append(format_html(f"<{element_name}{{}} />", flatatt(attributes)))

    return mark_safe("".join(rendered_nodes))


@register.simple_tag
def lucide(icon_name: str, **options: object) -> str:
    name = str(icon_name)
    if not _ICON_NAME_PATTERN.fullmatch(name):
        raise template.TemplateSyntaxError(f"Invalid Lucide icon name {name!r}.")

    class_name = str(options.pop("class", ""))
    label = options.pop("label", None)
    stroke_width = str(options.pop("stroke_width", "2"))
    if options:
        unsupported = ", ".join(sorted(options))
        raise template.TemplateSyntaxError(f"Unsupported lucide tag option(s): {unsupported}.")

    attributes = {
        "class": f"lucide lucide-{name} {class_name}".strip(),
        "fill": "none",
        "focusable": "false",
        "height": "24",
        "stroke": "currentColor",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        "stroke-width": stroke_width,
        "viewBox": "0 0 24 24",
        "width": "24",
        "xmlns": "http://www.w3.org/2000/svg",
    }
    if label:
        attributes.update({"aria-label": str(label), "role": "img"})
    else:
        attributes["aria-hidden"] = "true"

    return format_html("<svg{}>{}</svg>", flatatt(attributes), _render_icon_nodes(name))
