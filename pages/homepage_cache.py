import logging

from django.core.cache import cache
from django.utils import timezone

from agents.services import PretrainedWorkerTemplateService
from api.models import MCPServerConfig
from api.services.pipedream_apps import (
    PIPEDREAM_RUNTIME_NAME,
    PipedreamCatalogService,
    get_platform_pipedream_app_slugs,
)
from util.integrations import pipedream_status

logger = logging.getLogger(__name__)

HOMEPAGE_PRETRAINED_CACHE_VERSION = 1
HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS = 60
HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS = 600
HOMEPAGE_PRETRAINED_CACHE_LOCK_SECONDS = 60
HOMEPAGE_INTEGRATIONS_CACHE_VERSION = 1
HOMEPAGE_INTEGRATIONS_CACHE_FRESH_SECONDS = 60
HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS = 600
HOMEPAGE_INTEGRATIONS_CACHE_LOCK_SECONDS = 60


def _homepage_pretrained_cache_key() -> str:
    return f"pages:home:pretrained:v{HOMEPAGE_PRETRAINED_CACHE_VERSION}"


def _homepage_pretrained_cache_lock_key() -> str:
    return f"{_homepage_pretrained_cache_key()}:refresh_lock"


def _homepage_integrations_cache_key() -> str:
    return f"pages:home:integrations:v{HOMEPAGE_INTEGRATIONS_CACHE_VERSION}"


def _homepage_integrations_cache_lock_key() -> str:
    return f"{_homepage_integrations_cache_key()}:refresh_lock"


def _get_cached_payload(
    *,
    cache_key: str,
    fresh_seconds: int,
    stale_seconds: int,
    build_payload,
    enqueue_refresh,
) -> dict[str, object]:
    cached = cache.get(cache_key)
    now_ts = timezone.now().timestamp()

    if isinstance(cached, dict):
        cached_data = cached.get("data")
        refreshed_at = cached.get("refreshed_at")
        if cached_data is not None and refreshed_at is not None:
            age_seconds = max(0, now_ts - refreshed_at)
            if age_seconds <= fresh_seconds:
                return cached_data
            if age_seconds <= stale_seconds:
                enqueue_refresh()
                return cached_data

    payload = build_payload()
    cache.set(
        cache_key,
        {"data": payload, "refreshed_at": now_ts},
        timeout=stale_seconds,
    )
    return payload


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


def _platform_pipedream_server_is_active() -> bool:
    return MCPServerConfig.objects.filter(
        scope=MCPServerConfig.Scope.PLATFORM,
        name=PIPEDREAM_RUNTIME_NAME,
        is_active=True,
    ).exists()


def _build_homepage_integrations_payload() -> dict[str, object]:
    if not pipedream_status().enabled or not _platform_pipedream_server_is_active():
        return {"enabled": False, "builtins": []}

    app_slugs = get_platform_pipedream_app_slugs()
    if not app_slugs:
        return {"enabled": True, "builtins": []}

    builtins = [
        app.to_dict()
        for app in PipedreamCatalogService().get_apps(app_slugs)
    ]

    return {
        "enabled": True,
        "builtins": builtins,
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


def _enqueue_homepage_integrations_refresh() -> None:
    lock_key = _homepage_integrations_cache_lock_key()
    if not cache.add(lock_key, "1", timeout=HOMEPAGE_INTEGRATIONS_CACHE_LOCK_SECONDS):
        return

    try:
        from pages.tasks import refresh_homepage_integrations_cache

        refresh_homepage_integrations_cache.delay()
    except Exception:
        cache.delete(lock_key)
        logger.exception("Failed to enqueue homepage integrations refresh")


def get_homepage_pretrained_payload() -> dict[str, object]:
    return _get_cached_payload(
        cache_key=_homepage_pretrained_cache_key(),
        fresh_seconds=HOMEPAGE_PRETRAINED_CACHE_FRESH_SECONDS,
        stale_seconds=HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
        build_payload=_build_homepage_pretrained_payload,
        enqueue_refresh=_enqueue_homepage_pretrained_refresh,
    )


def get_homepage_integrations_payload() -> dict[str, object]:
    return _get_cached_payload(
        cache_key=_homepage_integrations_cache_key(),
        fresh_seconds=HOMEPAGE_INTEGRATIONS_CACHE_FRESH_SECONDS,
        stale_seconds=HOMEPAGE_INTEGRATIONS_CACHE_STALE_SECONDS,
        build_payload=_build_homepage_integrations_payload,
        enqueue_refresh=_enqueue_homepage_integrations_refresh,
    )
