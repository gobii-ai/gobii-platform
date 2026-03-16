"""Owner-scoped Pipedream app selection and catalog helpers."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, QuerySet

from api.pipedream_app_utils import normalize_app_slug, normalize_app_slugs
from api.models import MCPServerConfig, PersistentAgent, PersistentAgentEnabledTool, PipedreamAppSelection

logger = logging.getLogger(__name__)

PIPEDREAM_RUNTIME_NAME = "pipedream"
SEARCH_CACHE_TTL_SECONDS = 300
APP_CACHE_TTL_SECONDS = 1800
SEARCH_CACHE_PREFIX = "pipedream:apps:search:v1"
APP_CACHE_PREFIX = "pipedream:apps:item:v1"


class PipedreamCatalogError(RuntimeError):
    """Raised when the Pipedream catalog cannot be queried."""


@dataclass(frozen=True)
class PipedreamAppSummary:
    slug: str
    name: str
    description: str
    icon_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon_url": self.icon_url,
        }


@dataclass(frozen=True)
class PipedreamOwnerAppsState:
    owner_scope: str
    owner_label: str
    owner_id: str
    platform_app_slugs: list[str]
    selected_app_slugs: list[str]
    effective_app_slugs: list[str]


def serialize_owner_apps_state(
    state: PipedreamOwnerAppsState,
    catalog: Optional["PipedreamCatalogService"] = None,
) -> dict[str, object]:
    catalog_service = catalog or PipedreamCatalogService()
    app_lookup: dict[str, dict[str, str]] = {}
    for app in catalog_service.get_apps(
        normalize_app_slugs(
            [
                *state.platform_app_slugs,
                *state.selected_app_slugs,
                *state.effective_app_slugs,
            ]
        )
    ):
        app_data = app.to_dict()
        slug = str(app_data.get("slug") or "").strip()
        if slug and slug not in app_lookup:
            app_lookup[slug] = app_data
    return {
        "owner_scope": state.owner_scope,
        "owner_label": state.owner_label,
        "platform_apps": [app_lookup[slug] for slug in state.platform_app_slugs if slug in app_lookup],
        "selected_apps": [app_lookup[slug] for slug in state.selected_app_slugs if slug in app_lookup],
        "effective_apps": [app_lookup[slug] for slug in state.effective_app_slugs if slug in app_lookup],
    }


def _owner_queryset(owner_scope: str, owner_user=None, owner_org=None) -> QuerySet[PipedreamAppSelection]:
    queryset = PipedreamAppSelection.objects.all()
    if owner_scope == MCPServerConfig.Scope.ORGANIZATION:
        return queryset.filter(organization=owner_org)
    return queryset.filter(user=owner_user)


def build_owner_key(owner_scope: str, owner_id: str) -> str:
    return f"{owner_scope}:{owner_id}"


def owner_id_from_scope(owner_scope: str, owner_user=None, owner_org=None) -> str:
    if owner_scope == MCPServerConfig.Scope.ORGANIZATION and owner_org is not None:
        return str(owner_org.id)
    if owner_scope == MCPServerConfig.Scope.USER and owner_user is not None:
        return str(owner_user.id)
    raise ValueError(f"Unable to resolve owner id for scope '{owner_scope}'.")


def get_platform_pipedream_app_slugs() -> list[str]:
    config = (
        MCPServerConfig.objects.filter(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=PIPEDREAM_RUNTIME_NAME,
            is_active=True,
        )
        .only("prefetch_apps")
        .first()
    )
    if config is not None and config.prefetch_apps:
        return normalize_app_slugs(config.prefetch_apps)
    return normalize_app_slugs(str(settings.PIPEDREAM_PREFETCH_APPS).split(","))


def get_owner_selected_app_slugs(owner_scope: str, owner_user=None, owner_org=None) -> list[str]:
    selection = _owner_queryset(owner_scope, owner_user=owner_user, owner_org=owner_org).only("selected_app_slugs").first()
    if selection is None:
        return []
    return normalize_app_slugs(selection.selected_app_slugs or [])


def get_owner_apps_state(owner_scope: str, owner_label: str, owner_user=None, owner_org=None) -> PipedreamOwnerAppsState:
    owner_id = owner_id_from_scope(owner_scope, owner_user=owner_user, owner_org=owner_org)
    platform_app_slugs = get_platform_pipedream_app_slugs()
    selected_app_slugs = get_owner_selected_app_slugs(owner_scope, owner_user=owner_user, owner_org=owner_org)
    effective_app_slugs = normalize_app_slugs([*platform_app_slugs, *selected_app_slugs])
    return PipedreamOwnerAppsState(
        owner_scope=owner_scope,
        owner_label=owner_label,
        owner_id=owner_id,
        platform_app_slugs=platform_app_slugs,
        selected_app_slugs=selected_app_slugs,
        effective_app_slugs=effective_app_slugs,
    )


def get_effective_pipedream_app_slugs_for_agent(agent: PersistentAgent) -> list[str]:
    if agent.organization_id:
        state = get_owner_apps_state(
            MCPServerConfig.Scope.ORGANIZATION,
            agent.organization.name if agent.organization else "",
            owner_org=agent.organization,
        )
        return state.effective_app_slugs
    state = get_owner_apps_state(
        MCPServerConfig.Scope.USER,
        agent.user.get_full_name() or agent.user.username if agent.user else "",
        owner_user=agent.user,
    )
    return state.effective_app_slugs


def owner_agents_queryset(owner_scope: str, owner_user=None, owner_org=None) -> QuerySet[PersistentAgent]:
    queryset = PersistentAgent.objects.non_eval().alive()
    if owner_scope == MCPServerConfig.Scope.ORGANIZATION:
        return queryset.filter(organization=owner_org)
    return queryset.filter(user=owner_user, organization_id__isnull=True)


def _delete_disabled_enabled_tools(
    owner_scope: str,
    *,
    disabled_app_slugs: Iterable[str],
    owner_user=None,
    owner_org=None,
) -> None:
    disabled = normalize_app_slugs(disabled_app_slugs)
    if not disabled:
        return

    pipedream_server_ids = list(
        MCPServerConfig.objects.filter(name=PIPEDREAM_RUNTIME_NAME).values_list("id", flat=True)
    )
    if not pipedream_server_ids:
        return

    agent_ids = list(owner_agents_queryset(owner_scope, owner_user=owner_user, owner_org=owner_org).values_list("id", flat=True))
    if not agent_ids:
        return

    for slug in disabled:
        prefix = f"{slug}-"
        PersistentAgentEnabledTool.objects.filter(
            agent_id__in=agent_ids,
        ).filter(
            Q(server_config_id__in=pipedream_server_ids) | Q(tool_server=PIPEDREAM_RUNTIME_NAME)
        ).filter(
            Q(tool_name__startswith=prefix) | Q(tool_full_name__startswith=prefix)
        ).delete()


def set_owner_selected_app_slugs(owner_scope: str, selected_app_slugs: Iterable[object], owner_user=None, owner_org=None) -> list[str]:
    platform_app_slugs = get_platform_pipedream_app_slugs()
    platform_set = set(platform_app_slugs)
    normalized = [slug for slug in normalize_app_slugs(selected_app_slugs, strict=True) if slug not in platform_set]
    prior = get_owner_selected_app_slugs(owner_scope, owner_user=owner_user, owner_org=owner_org)

    with transaction.atomic():
        selection = _owner_queryset(owner_scope, owner_user=owner_user, owner_org=owner_org).select_for_update().first()
        if not normalized:
            if selection is not None:
                selection.delete()
        else:
            if selection is None:
                selection = PipedreamAppSelection(
                    organization=owner_org if owner_scope == MCPServerConfig.Scope.ORGANIZATION else None,
                    user=owner_user if owner_scope != MCPServerConfig.Scope.ORGANIZATION else None,
                )
            selection.selected_app_slugs = normalized
            selection.full_clean()
            selection.save()

    removed = [slug for slug in prior if slug not in normalized]
    effective = normalize_app_slugs([*platform_app_slugs, *normalized])
    _delete_disabled_enabled_tools(
        owner_scope,
        disabled_app_slugs=[slug for slug in removed if slug not in effective],
        owner_user=owner_user,
        owner_org=owner_org,
    )
    return normalized


def _agent_owner_info(agent: PersistentAgent) -> tuple[str, str, Any | None, Any | None, str]:
    if agent.organization_id:
        owner_scope = MCPServerConfig.Scope.ORGANIZATION
        owner_label = agent.organization.name if agent.organization else ""
        owner_org = agent.organization
        owner_user = None
        owner_id = owner_id_from_scope(owner_scope, owner_org=owner_org)
        return owner_scope, owner_label, owner_user, owner_org, owner_id

    owner_scope = MCPServerConfig.Scope.USER
    owner_label = agent.user.get_full_name() or agent.user.username if agent.user else ""
    owner_user = agent.user
    owner_org = None
    owner_id = owner_id_from_scope(owner_scope, owner_user=owner_user)
    return owner_scope, owner_label, owner_user, owner_org, owner_id


def enable_pipedream_apps_for_agent(
    agent: PersistentAgent,
    app_slugs: Iterable[object],
    *,
    available_app_slugs: Optional[Iterable[object]] = None,
) -> dict[str, object]:
    if available_app_slugs is None:
        return {
            "status": "error",
            "message": "available_app_slugs is required",
            "enabled": [],
            "already_enabled": [],
            "invalid": [],
            "selected": [],
            "effective_apps": get_effective_pipedream_app_slugs_for_agent(agent),
        }

    requested: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    for value in app_slugs or []:
        normalized = normalize_app_slug(value)
        if not normalized:
            if value is not None:
                invalid.append(str(value))
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        requested.append(normalized)

    available_set = set(normalize_app_slugs(available_app_slugs))

    valid_requested = [slug for slug in requested if slug in available_set]
    invalid.extend(slug for slug in requested if slug not in available_set)

    owner_scope, owner_label, owner_user, owner_org, owner_id = _agent_owner_info(agent)
    platform_set = set(get_platform_pipedream_app_slugs())
    prior_selected = get_owner_selected_app_slugs(owner_scope, owner_user=owner_user, owner_org=owner_org)
    prior_selected_set = set(prior_selected)

    enabled = [
        slug for slug in valid_requested
        if slug not in platform_set and slug not in prior_selected_set
    ]
    already_enabled = [slug for slug in valid_requested if slug in platform_set or slug in prior_selected_set]

    if enabled:
        merged_selected = normalize_app_slugs([*prior_selected, *enabled])
        selected = set_owner_selected_app_slugs(
            owner_scope,
            merged_selected,
            owner_user=owner_user,
            owner_org=owner_org,
        )
        from api.agent.tools.mcp_manager import get_mcp_manager

        manager = get_mcp_manager()
        manager.invalidate_pipedream_owner_cache(owner_scope, owner_id)
        manager.prewarm_pipedream_owner_cache(owner_scope, owner_id, app_slugs=selected)

    state = get_owner_apps_state(owner_scope, owner_label, owner_user=owner_user, owner_org=owner_org)
    return {
        "status": "success",
        "enabled": enabled,
        "already_enabled": already_enabled,
        "invalid": invalid,
        "selected": state.selected_app_slugs,
        "effective_apps": state.effective_app_slugs,
    }


def _search_cache_key(query: str, limit: int) -> str:
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    return f"{SEARCH_CACHE_PREFIX}:{query_hash}:{limit}"


def _app_cache_key(slug: str) -> str:
    return f"{APP_CACHE_PREFIX}:{slug}"


def _extract_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return [payload]
    return []


def _deserialize_app(item: dict[str, object], *, fallback_slug: str = "") -> Optional[PipedreamAppSummary]:
    slug = normalize_app_slug(
        item.get("name_slug") or item.get("slug") or item.get("id") or fallback_slug
    )
    if not slug:
        return None
    name = str(item.get("name") or item.get("key") or slug).strip() or slug
    description = str(item.get("description_short") or item.get("description") or "").strip()
    icon_url = str(
        item.get("img_src")
        or item.get("icon_url")
        or item.get("featured_image_url")
        or ""
    ).strip()
    return PipedreamAppSummary(
        slug=slug,
        name=name,
        description=description,
        icon_url=icon_url,
    )


class PipedreamCatalogService:
    """Search and hydrate apps from Pipedream's official app catalog."""

    api_base_url = "https://api.pipedream.com/v1"

    def _headers(self) -> dict[str, str]:
        from api.agent.tools.mcp_manager import get_pipedream_access_token

        token = get_pipedream_access_token()
        if not token:
            raise PipedreamCatalogError("Pipedream access token unavailable.")
        return {"Authorization": f"Bearer {token}"}

    def _request_json(self, path: str, *, params: dict[str, object] | None = None) -> object:
        try:
            response = requests.get(
                f"{self.api_base_url}{path}",
                params=params or None,
                headers=self._headers(),
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise PipedreamCatalogError("Failed to query the Pipedream app catalog.") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise PipedreamCatalogError("Pipedream app catalog returned invalid JSON.") from exc

    def search_apps(self, query: str, *, limit: int = 20) -> list[PipedreamAppSummary]:
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            return []

        cache_key = _search_cache_key(normalized_query, limit)
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            return [PipedreamAppSummary(**item) for item in cached if isinstance(item, dict)]

        payload = self._request_json(
            "/apps",
            params={"q": normalized_query, "limit": max(1, min(int(limit), 50)), "has_actions": "true"},
        )
        results: list[PipedreamAppSummary] = []
        for item in _extract_items(payload):
            parsed = _deserialize_app(item)
            if parsed is None:
                continue
            results.append(parsed)
        cache.set(cache_key, [item.to_dict() for item in results], SEARCH_CACHE_TTL_SECONDS)
        return results

    def get_app(self, slug: str) -> PipedreamAppSummary:
        normalized_slug = normalize_app_slug(slug)
        if not normalized_slug:
            raise PipedreamCatalogError("App slug is required.")

        cache_key = _app_cache_key(normalized_slug)
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return PipedreamAppSummary(**cached)

        payload = self._request_json(f"/apps/{normalized_slug}")
        items = _extract_items(payload)
        parsed = _deserialize_app(items[0], fallback_slug=normalized_slug) if items else None
        if parsed is None:
            parsed = PipedreamAppSummary(
                slug=normalized_slug,
                name=normalized_slug.replace("_", " "),
                description="",
                icon_url="",
            )
        cache.set(cache_key, parsed.to_dict(), APP_CACHE_TTL_SECONDS)
        return parsed

    def get_apps(self, slugs: Iterable[str]) -> list[PipedreamAppSummary]:
        normalized_slugs = normalize_app_slugs(slugs)
        if not normalized_slugs:
            return []

        results: list[PipedreamAppSummary] = []
        max_workers = min(len(normalized_slugs), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                slug: executor.submit(self.get_app, slug)
                for slug in normalized_slugs
            }
            for slug in normalized_slugs:
                try:
                    results.append(futures[slug].result())
                except PipedreamCatalogError:
                    logger.warning("Failed to hydrate Pipedream app '%s'", slug, exc_info=True)
                    results.append(
                        PipedreamAppSummary(
                            slug=slug,
                            name=slug.replace("_", " "),
                            description="",
                            icon_url="",
                        )
                    )
        return results
