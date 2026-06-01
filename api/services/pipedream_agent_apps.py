"""Agent-scoped Pipedream app orchestration for console APIs."""

from dataclasses import dataclass
from typing import Any

from django.http import Http404

from api.agent.tools.mcp_manager import get_mcp_manager
from api.models import MCPServerConfig, PersistentAgent
from api.pipedream_app_utils import normalize_app_slug, normalize_app_slugs
from api.services.pipedream_apps import (
    PipedreamCatalogService,
    filter_deprecated_pipedream_apps_for_agent,
    get_platform_pipedream_app_slugs,
    get_owner_apps_state,
    is_pipedream_app_visible_to_agent,
    owner_agents_queryset,
    set_owner_selected_app_slugs,
)
from api.services.pipedream_connections import (
    PipedreamConnectionError,
    delete_pipedream_connected_accounts,
    group_pipedream_connected_accounts_by_app,
    invalidate_pipedream_connected_accounts_cache,
    list_pipedream_connected_accounts,
)


@dataclass(frozen=True)
class PipedreamOwnerContext:
    scope: str
    label: str
    user: Any | None
    organization: Any | None
    id: str


def pipedream_owner_for_agent(agent: PersistentAgent) -> PipedreamOwnerContext:
    if agent.organization_id:
        owner_org = agent.organization
        if owner_org is None:
            raise Http404("Organization not found")
        return PipedreamOwnerContext(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            label=owner_org.name,
            user=None,
            organization=owner_org,
            id=str(owner_org.id),
        )
    if agent.user is None:
        raise Http404("Agent owner not found")
    return PipedreamOwnerContext(
        scope=MCPServerConfig.Scope.USER,
        label=agent.user.get_full_name() or agent.user.username,
        user=agent.user,
        organization=None,
        id=str(agent.user.id),
    )


def _owner_state(owner: PipedreamOwnerContext):
    return get_owner_apps_state(
        owner.scope,
        owner.label,
        owner_user=owner.user,
        owner_org=owner.organization,
    )


def _refresh_owner_cache(owner: PipedreamOwnerContext, app_slugs: list[str]) -> None:
    manager = get_mcp_manager()
    manager.invalidate_pipedream_owner_cache(owner.scope, owner.id)
    manager.prewarm_pipedream_owner_cache(owner.scope, owner.id, app_slugs=app_slugs)


def _serialize_agent_app_row(
    app: dict[str, str],
    *,
    source: str,
    account_ids: list[str],
) -> dict[str, object]:
    return {
        "slug": app.get("slug", ""),
        "name": app.get("name", app.get("slug", "")),
        "description": app.get("description", ""),
        "icon_url": app.get("icon_url", ""),
        "source": source,
        "connected": bool(account_ids),
        "account_ids": account_ids,
    }


def list_agent_pipedream_app_rows(agent: PersistentAgent, *, query: str = "") -> dict[str, object]:
    owner = pipedream_owner_for_agent(agent)
    state = _owner_state(owner)
    platform_set = set(state.platform_app_slugs)
    selected_set = set(state.selected_app_slugs)
    normalized_query = str(query or "").strip()
    catalog = PipedreamCatalogService()
    try:
        connected_accounts = list_pipedream_connected_accounts(agent)
    except PipedreamConnectionError:
        connected_accounts = []
    connected_by_app = group_pipedream_connected_accounts_by_app(connected_accounts)
    connected_app_slugs = set(connected_by_app)

    if normalized_query:
        search_results = catalog.search_apps(normalized_query, limit=30)
        search_results = filter_deprecated_pipedream_apps_for_agent(
            agent,
            search_results,
            connected_app_slugs=connected_app_slugs,
        )
        apps = {app.slug: app.to_dict() for app in search_results}
        ordered_slugs = normalize_app_slugs(app.slug for app in search_results)
    else:
        apps = {
            app.slug: app.to_dict()
            for app in catalog.get_apps(state.effective_app_slugs)
        }
        ordered_slugs = state.effective_app_slugs

    rows = []
    for slug in ordered_slugs:
        if not is_pipedream_app_visible_to_agent(agent, slug, connected_app_slugs=connected_app_slugs):
            continue
        app = apps.get(slug)
        if app is None:
            continue
        if slug in platform_set:
            source = "built_in"
        elif slug in selected_set:
            source = "added"
        else:
            source = "available"
        rows.append(
            _serialize_agent_app_row(
                app,
                source=source,
                account_ids=connected_by_app.get(slug, []),
            )
        )

    return {
        "agent_id": str(agent.id),
        "owner_scope": owner.scope,
        "owner_label": owner.label,
        "query": normalized_query,
        "apps": rows,
    }


def remove_agent_pipedream_app(agent: PersistentAgent, app_slug: str) -> dict[str, object]:
    normalized_slug = normalize_app_slug(app_slug)
    if not normalized_slug:
        raise ValueError("app_slug is required.")

    owner = pipedream_owner_for_agent(agent)
    state = _owner_state(owner)
    if normalized_slug in set(state.platform_app_slugs):
        raise ValueError("Built-in apps cannot be removed.")

    next_selected = [slug for slug in state.selected_app_slugs if slug != normalized_slug]
    if next_selected == state.selected_app_slugs:
        return {
            "app_slug": normalized_slug,
            "removed": False,
            "selected_app_slugs": state.selected_app_slugs,
        }

    selected = set_owner_selected_app_slugs(
        owner.scope,
        next_selected,
        owner_user=owner.user,
        owner_org=owner.organization,
    )
    _refresh_owner_cache(owner, selected)
    return {
        "app_slug": normalized_slug,
        "removed": True,
        "selected_app_slugs": selected,
    }


def start_agent_pipedream_app_connect(agent: PersistentAgent, app_slug: str) -> dict[str, object]:
    normalized_slug = normalize_app_slug(app_slug)
    if not normalized_slug:
        raise ValueError("app_slug is required.")

    if not is_pipedream_app_visible_to_agent(agent, normalized_slug):
        raise ValueError("This Pipedream app is deprecated and cannot be newly connected.")

    catalog = PipedreamCatalogService()
    app = catalog.get_app(normalized_slug)
    owner = pipedream_owner_for_agent(agent)
    state = _owner_state(owner)
    selected = state.selected_app_slugs

    platform_set = set(get_platform_pipedream_app_slugs())
    if normalized_slug not in platform_set and normalized_slug not in set(state.effective_app_slugs):
        selected = set_owner_selected_app_slugs(
            owner.scope,
            [*state.selected_app_slugs, normalized_slug],
            owner_user=owner.user,
            owner_org=owner.organization,
        )
        _refresh_owner_cache(owner, selected)

    return {
        "app": app.to_dict(),
        "selected_app_slugs": selected,
    }


def disconnect_agent_pipedream_app(agent: PersistentAgent, app_slug: str) -> dict[str, object]:
    normalized_slug = normalize_app_slug(app_slug)
    if not normalized_slug:
        raise ValueError("app_slug is required.")

    accounts = list_pipedream_connected_accounts(agent, app_slug=normalized_slug)
    deleted_count = delete_pipedream_connected_accounts(account.id for account in accounts)
    invalidate_pipedream_connected_accounts_cache(agent, app_slug=normalized_slug)
    return {
        "app_slug": normalized_slug,
        "connected": False,
        "deleted_count": deleted_count,
    }


def list_pipedream_app_agent_connections(
    *,
    owner_scope: str,
    owner_user: Any | None,
    owner_org: Any | None,
    app_slug: str,
) -> dict[str, object]:
    normalized_slug = normalize_app_slug(app_slug)
    if not normalized_slug:
        raise ValueError("app_slug is required.")

    agents = list(
        owner_agents_queryset(owner_scope, owner_user=owner_user, owner_org=owner_org)
        .only("id", "name", "avatar", "updated_at")
        .order_by("name", "id")
    )
    connected_by_agent: dict[str, list[str]] = {}
    for agent in agents:
        accounts = list_pipedream_connected_accounts(agent, app_slug=normalized_slug)
        if accounts:
            connected_by_agent[str(agent.id)] = [account.id for account in accounts]

    rows = []
    for agent in agents:
        account_ids = connected_by_agent.get(str(agent.id), [])
        rows.append(
            {
                "agent_id": str(agent.id),
                "name": agent.name,
                "avatar_url": agent.get_avatar_thumbnail_url() or "",
                "connected": bool(account_ids),
                "account_ids": account_ids,
            }
        )

    rows.sort(key=lambda row: (not row["connected"], str(row["name"]).lower(), str(row["agent_id"])))
    return {"app_slug": normalized_slug, "agents": rows}
