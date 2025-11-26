"""Helpers for resolving MCP server availability for agents."""

from typing import Iterable, Iterable as IterableType, List, Dict, Any, Set

from django.db import transaction

from api.models import (
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentMCPServer,
    PersistentAgentEnabledTool,
)
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


def platform_server_configs() -> Iterable[MCPServerConfig]:
    """Return active platform-scoped MCP server configs."""

    return MCPServerConfig.objects.filter(
        scope=MCPServerConfig.Scope.PLATFORM,
        is_active=True,
    )


def organization_server_configs(org_id) -> Iterable[MCPServerConfig]:
    """Return active organization-scoped MCP server configs for the given org."""

    if not org_id:
        return MCPServerConfig.objects.none()

    return MCPServerConfig.objects.filter(
        scope=MCPServerConfig.Scope.ORGANIZATION,
        organization_id=org_id,
        is_active=True,
    )


def personal_server_configs(user_id) -> Iterable[MCPServerConfig]:
    """Return active user-scoped MCP server configs for the given user."""

    if not user_id:
        return MCPServerConfig.objects.none()

    return MCPServerConfig.objects.filter(
        scope=MCPServerConfig.Scope.USER,
        user_id=user_id,
        is_active=True,
    )


def agent_enabled_server_ids(agent: PersistentAgent) -> List[str]:
    """Return MCP server IDs explicitly enabled for the agent."""

    return [
        str(server_id)
        for server_id in PersistentAgentMCPServer.objects.filter(agent=agent)
        .values_list('server_config_id', flat=True)
    ]


def agent_enabled_personal_server_ids(agent: PersistentAgent) -> List[str]:
    """Return user-scoped MCP server IDs enabled for the agent."""

    return [
        str(server_id)
        for server_id in PersistentAgentMCPServer.objects.filter(
            agent=agent,
            server_config__scope=MCPServerConfig.Scope.USER,
        ).values_list('server_config_id', flat=True)
    ]


def _assignable_agents_queryset(server: MCPServerConfig):
    """Return queryset of agents eligible for assignment to the given server."""

    if server.scope == MCPServerConfig.Scope.USER:
        if not server.user_id:
            return PersistentAgent.objects.none()
        return PersistentAgent.objects.filter(user_id=server.user_id, organization_id__isnull=True)
    if server.scope == MCPServerConfig.Scope.ORGANIZATION:
        if not server.organization_id:
            return PersistentAgent.objects.none()
        return PersistentAgent.objects.filter(organization_id=server.organization_id)
    return PersistentAgent.objects.none()


def assignable_agents(server: MCPServerConfig):
    """Return agents eligible for assignment to the given server."""

    return _assignable_agents_queryset(server).order_by('name', 'created_at')


def server_assignment_agent_ids(server: MCPServerConfig) -> Set[str]:
    """Return the set of agent IDs explicitly assigned to this server."""

    return {
        str(agent_id)
        for agent_id in PersistentAgentMCPServer.objects.filter(server_config=server)
        .values_list('agent_id', flat=True)
    }


def set_server_assignments(server: MCPServerConfig, desired_agent_ids: IterableType[str]) -> None:
    """Assign the given server to the provided collection of agents."""

    if server.scope == MCPServerConfig.Scope.PLATFORM:
        raise ValueError("Platform-scoped servers cannot be assigned manually.")

    desired_set = {str(agent_id) for agent_id in desired_agent_ids}
    assignable_qs = _assignable_agents_queryset(server)
    assignable_map = {
        str(agent.id): agent for agent in assignable_qs.only('id')
    }

    invalid = desired_set - set(assignable_map.keys())
    if invalid:
        raise ValueError(f"Invalid agent ids for this server: {', '.join(sorted(invalid))}")

    existing = server_assignment_agent_ids(server)
    to_add = desired_set - existing
    to_remove = existing - desired_set

    if not to_add and not to_remove:
        return

    with transaction.atomic():
        if to_add:
            PersistentAgentMCPServer.objects.bulk_create(
                [
                    PersistentAgentMCPServer(agent_id=agent_id, server_config=server)
                    for agent_id in to_add
                ],
                ignore_conflicts=True,
            )

        if to_remove:
            PersistentAgentMCPServer.objects.filter(
                agent_id__in=to_remove,
                server_config=server,
            ).delete()
            PersistentAgentEnabledTool.objects.filter(
                agent_id__in=to_remove,
                server_config=server,
            ).delete()

        unassigned_ids = set(assignable_map.keys()) - desired_set
        if unassigned_ids:
            PersistentAgentEnabledTool.objects.filter(
                agent_id__in=unassigned_ids,
                server_config=server,
            ).delete()



def agent_accessible_server_configs(agent: PersistentAgent) -> List[MCPServerConfig]:
    """Collect all MCP server configs accessible to the agent."""

    assigned_ids = set(agent_enabled_server_ids(agent))
    configs: list[MCPServerConfig] = []
    seen: Set[str] = set()

    def _add(cfg: MCPServerConfig):
        server_id = str(cfg.id)
        if server_id in seen:
            return
        seen.add(server_id)
        configs.append(cfg)

    for cfg in platform_server_configs():
        _add(cfg)

    if agent.organization_id:
        for cfg in organization_server_configs(agent.organization_id):
            server_id = str(cfg.id)
            if server_id not in assigned_ids:
                continue
            _add(cfg)

    for cfg in personal_server_configs(agent.user_id):
        server_id = str(cfg.id)
        if server_id not in assigned_ids:
            continue
        _add(cfg)

    return sorted(
        configs,
        key=lambda cfg: ((cfg.display_name or '').lower(), (cfg.name or '').lower()),
    )


def agent_server_overview(agent: PersistentAgent) -> List[Dict[str, Any]]:
    """Return structured info about MCP servers available to an agent."""

    overview: List[Dict[str, Any]] = []
    assigned_ids = set(agent_enabled_server_ids(agent))

    if agent.organization_id:
        for cfg in organization_server_configs(agent.organization_id):
            server_id = str(cfg.id)
            overview.append(
                _serialize_config(
                    cfg,
                    inherited=False,
                    assigned=server_id in assigned_ids,
                )
            )

    for cfg in platform_server_configs():
        overview.append(
            _serialize_config(cfg, inherited=True, assigned=True)
        )

    for cfg in personal_server_configs(agent.user_id):
        server_id = str(cfg.id)
        overview.append(
            _serialize_config(
                cfg,
                inherited=False,
                assigned=server_id in assigned_ids,
            )
        )

    return overview


def update_agent_personal_servers(
    agent: PersistentAgent,
    desired_ids: List[str],
    *,
    actor_user_id: int | str | None = None,
    source: AnalyticsSource | None = None,
) -> None:
    """Set the personal (user-scoped) servers enabled for an agent."""

    desired_set = {str(pk) for pk in desired_ids}
    existing_set = set(agent_enabled_server_ids(agent))

    if not desired_set and not existing_set:
        return

    valid_ids = {
        str(server_id)
        for server_id in MCPServerConfig.objects.filter(
            scope=MCPServerConfig.Scope.USER,
            user=agent.user,
            is_active=True,
            id__in=desired_set,
        ).values_list('id', flat=True)
    }

    invalid = desired_set - valid_ids
    if invalid:
        raise ValueError(f"Invalid personal MCP server ids: {', '.join(sorted(invalid))}")

    to_add = valid_ids - existing_set
    to_remove = existing_set - desired_set

    if to_add:
        PersistentAgentMCPServer.objects.bulk_create(
            [
                PersistentAgentMCPServer(agent=agent, server_config_id=server_id)
                for server_id in to_add
            ]
        )

    if to_remove:
        PersistentAgentMCPServer.objects.filter(
            agent=agent,
            server_config_id__in=to_remove,
        ).delete()

        # Remove any enabled tools bound to removed servers
        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            server_config_id__in=to_remove,
        ).delete()

    # Ensure no enabled tools remain for servers outside the accessible set
    accessible_configs = {
        str(cfg.id)
        for cfg in agent_accessible_server_configs(agent)
    }

    PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        server_config_id__isnull=False,
    ).exclude(
        server_config_id__in=accessible_configs
    ).delete()

    _emit_personal_server_events(
        agent,
        added_ids=to_add,
        removed_ids=to_remove,
        actor_user_id=actor_user_id,
        source=source,
    )


def _emit_personal_server_events(
    agent: PersistentAgent,
    *,
    added_ids: Set[str],
    removed_ids: Set[str],
    actor_user_id: int | str | None,
    source: AnalyticsSource | None,
) -> None:
    """Emit analytics for personal MCP server link/unlink actions."""

    if not actor_user_id or not source:
        return

    if not added_ids and not removed_ids:
        return

    target_ids = added_ids | removed_ids
    server_map = {
        str(cfg.id): cfg
        for cfg in MCPServerConfig.objects.filter(id__in=list(target_ids)).only('id', 'name', 'display_name', 'scope')
    }

    def _track(event_type: AnalyticsEvent, server_id: str) -> None:
        cfg = server_map.get(server_id)
        props = {
            'agent_id': str(agent.id),
            'agent_name': agent.name,
            'mcp_server_id': server_id,
        }
        if cfg:
            props['mcp_server_name'] = cfg.name
            props['mcp_server_display_name'] = cfg.display_name
            props['mcp_server_scope'] = cfg.scope

        props = Analytics.with_org_properties(props, organization=agent.organization)
        Analytics.track_event(
            user_id=actor_user_id,
            event=event_type,
            source=source,
            properties=props.copy(),
        )

    for server_id in sorted(added_ids):
        _track(AnalyticsEvent.PERSISTENT_AGENT_MCP_LINKED, server_id)

    for server_id in sorted(removed_ids):
        _track(AnalyticsEvent.PERSISTENT_AGENT_MCP_UNLINKED, server_id)


def update_agent_org_servers(
    agent: PersistentAgent,
    desired_ids: List[str],
    *,
    actor_user_id: int | str | None = None,
    source: AnalyticsSource | None = None,
) -> None:
    """Set the org-scoped servers enabled for an org agent."""

    if not agent.organization_id:
        raise ValueError("Organization MCP servers can only be configured for organization agents.")

    desired_set = {str(pk) for pk in desired_ids}
    existing_set = set(agent_enabled_server_ids(agent))

    if not desired_set and not existing_set:
        return

    valid_ids = {
        str(server_id)
        for server_id in MCPServerConfig.objects.filter(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization_id=agent.organization_id,
            is_active=True,
            id__in=desired_set,
        ).values_list('id', flat=True)
    }

    invalid = desired_set - valid_ids
    if invalid:
        raise ValueError(f"Invalid organization MCP server ids: {', '.join(sorted(invalid))}")

    to_add = valid_ids - existing_set
    to_remove = existing_set - desired_set

    if to_add:
        PersistentAgentMCPServer.objects.bulk_create(
            [
                PersistentAgentMCPServer(agent=agent, server_config_id=server_id)
                for server_id in to_add
            ],
            ignore_conflicts=True,
        )

    if to_remove:
        PersistentAgentMCPServer.objects.filter(
            agent=agent,
            server_config_id__in=to_remove,
        ).delete()

        PersistentAgentEnabledTool.objects.filter(
            agent=agent,
            server_config_id__in=to_remove,
        ).delete()

    accessible_configs = {
        str(cfg.id)
        for cfg in agent_accessible_server_configs(agent)
    }

    PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        server_config_id__isnull=False,
    ).exclude(
        server_config_id__in=accessible_configs
    ).delete()

    _emit_personal_server_events(
        agent,
        added_ids=to_add,
        removed_ids=to_remove,
        actor_user_id=actor_user_id,
        source=source,
    )


def _serialize_config(cfg: MCPServerConfig, *, inherited: bool, assigned: bool) -> Dict[str, Any]:
    return {
        'id': str(cfg.id),
        'name': cfg.name,
        'display_name': cfg.display_name,
        'description': cfg.description,
        'scope': cfg.scope,
        'inherited': inherited,
        'assigned': assigned,
        'is_active': cfg.is_active,
        'organization_id': str(cfg.organization_id) if cfg.organization_id else None,
        'user_id': str(cfg.user_id) if cfg.user_id else None,
    }
