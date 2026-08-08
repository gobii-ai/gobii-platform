import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.safestring import mark_safe
from rjsmin import jsmin


register = template.Library()

_SCRIPT_END_PATTERN = re.compile(r"</script", re.IGNORECASE)


@lru_cache(maxsize=32)
def _resolve_javascript_path(static_path: str) -> Path:
    relative_path = PurePosixPath(static_path)
    if relative_path.is_absolute() or ".." in relative_path.parts or relative_path.suffix != ".js":
        raise template.TemplateSyntaxError(
            f"inline_minified_static_js requires a relative .js path, got {static_path!r}."
        )

    resolved_path = finders.find(static_path)
    if isinstance(resolved_path, list):
        resolved_path = resolved_path[0] if resolved_path else None
    if not resolved_path:
        raise template.TemplateSyntaxError(f"Static JavaScript file {static_path!r} was not found.")

    return Path(resolved_path)


@lru_cache(maxsize=32)
def _minify_javascript(resolved_path: Path, version: int | None) -> str:
    source = resolved_path.read_text(encoding="utf-8")
    minified = jsmin(source)
    # Browsers terminate an inline script at this byte sequence, even inside a JS string.
    return _SCRIPT_END_PATTERN.sub(r"<\\/script", minified)


@register.simple_tag
def inline_minified_static_js(static_path: str) -> str:
    resolved_path = _resolve_javascript_path(static_path)
    version = resolved_path.stat().st_mtime_ns if settings.DEBUG else None
    return mark_safe(_minify_javascript(resolved_path, version))
