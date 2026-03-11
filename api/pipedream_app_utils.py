"""Shared normalization helpers for Pipedream app slugs."""

from collections.abc import Iterable


def normalize_app_slug(value: object, *, strict: bool = False) -> str:
    if not isinstance(value, str):
        if strict:
            raise ValueError("selected_app_slugs must be a list of strings.")
        return ""
    return value.strip().lower().replace(" ", "_")


def normalize_app_slugs(
    values: Iterable[object] | None,
    *,
    strict: bool = False,
    require_list: bool = False,
) -> list[str]:
    if values is None:
        return []
    if require_list and not isinstance(values, list):
        raise ValueError("selected_app_slugs must be a list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        slug = normalize_app_slug(value, strict=strict)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        normalized.append(slug)
    return normalized
