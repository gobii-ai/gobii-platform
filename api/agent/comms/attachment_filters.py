import os
from typing import Iterable

from config import settings


def _normalize_content_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _normalize_list(values: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip().lower()
        if item:
            normalized.append(item)
    return tuple(normalized)


def is_signature_image_attachment(filename: str, content_type: str) -> bool:
    base_name = os.path.basename(filename or "")
    if not base_name:
        return False
    lowered = base_name.lower()
    prefixes = _normalize_list(getattr(settings, "SIGNATURE_IMAGE_ATTACHMENT_PREFIXES", ()))
    if not prefixes:
        return False
    if not any(lowered.startswith(prefix) for prefix in prefixes):
        return False
    normalized_type = _normalize_content_type(content_type)
    if normalized_type.startswith("image/"):
        return True
    _, ext = os.path.splitext(base_name)
    extensions = _normalize_list(getattr(settings, "SIGNATURE_IMAGE_ATTACHMENT_EXTENSIONS", ()))
    return ext.lower() in extensions
