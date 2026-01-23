import logging

from django.core.cache import cache
from django.utils import timezone

from agents.services import PretrainedWorkerTemplateService

logger = logging.getLogger(__name__)

HOMEPAGE_PRETRAINED_CACHE_VERSION = 1
HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS = 60
HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS = 600
HOMEPAGE_PRETRAINED_CACHE_LOCK_SECONDS = 60


def _homepage_pretrained_cache_key() -> str:
    return f"pages:home:pretrained:v{HOMEPAGE_PRETRAINED_CACHE_VERSION}"


def _homepage_pretrained_cache_lock_key() -> str:
    return f"{_homepage_pretrained_cache_key()}:refresh_lock"


def _serialize_template(template, display_map: dict[str, str]) -> dict[str, object]:
    default_tools = list(template.default_tools or [])
    return {
        "code": template.code,
        "display_name": template.display_name,
        "tagline": template.tagline,
        "description": template.description,
        "charter": template.charter,
        "base_schedule": template.base_schedule,
        "schedule_jitter_minutes": template.schedule_jitter_minutes,
        "event_triggers": list(template.event_triggers or []),
        "default_tools": default_tools,
        "recommended_contact_channel": template.recommended_contact_channel,
        "category": template.category,
        "hero_image_path": template.hero_image_path,
        "priority": template.priority,
        "is_active": template.is_active,
        "show_on_homepage": template.show_on_homepage,
        "schedule_description": PretrainedWorkerTemplateService.describe_schedule(
            template.base_schedule
        ),
        "display_default_tools": PretrainedWorkerTemplateService.get_tool_display_list(
            default_tools,
            display_map=display_map,
        ),
    }


def _build_homepage_pretrained_payload() -> dict[str, object]:
    templates = list(PretrainedWorkerTemplateService.get_active_templates())
    if not templates:
        return {"templates": [], "categories": [], "total": 0}

    tool_names = set()
    for template in templates:
        tool_names.update(template.default_tools or [])

    display_map = PretrainedWorkerTemplateService.get_tool_display_map(tool_names)
    payload_templates = [
        _serialize_template(template, display_map) for template in templates
    ]
    categories = sorted(
        {template.category for template in templates if template.category}
    )

    return {
        "templates": payload_templates,
        "categories": categories,
        "total": len(payload_templates),
    }


def _enqueue_homepage_pretrained_refresh() -> None:
    lock_key = _homepage_pretrained_cache_lock_key()
    if not cache.add(lock_key, "1", timeout=HOMEPAGE_PRETRAINED_CACHE_LOCK_SECONDS):
        return

    try:
        from pages.tasks import refresh_homepage_pretrained_cache

        refresh_homepage_pretrained_cache.delay()
    except Exception:
        cache.delete(lock_key)
        logger.exception("Failed to enqueue homepage pretrained refresh")


def get_homepage_pretrained_payload() -> dict[str, object]:
    cache_key = _homepage_pretrained_cache_key()
    cached = cache.get(cache_key)
    now_ts = timezone.now().timestamp()

    if isinstance(cached, dict):
        cached_data = cached.get("data")
        refreshed_at = cached.get("refreshed_at")
        if cached_data is not None and refreshed_at is not None:
            age_seconds = max(0, now_ts - refreshed_at)
            if age_seconds <= HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS:
                return cached_data
            if age_seconds <= HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS:
                _enqueue_homepage_pretrained_refresh()
                return cached_data

    payload = _build_homepage_pretrained_payload()
    cache.set(
        cache_key,
        {"data": payload, "refreshed_at": now_ts},
        timeout=HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
    )
    return payload
