"""Helpers for resolving MCP server availability for agents."""

from typing import Iterable, List, Dict, Any

from django.db.models import Q

from api.models import (
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentMCPServer,
    PersistentAgentEnabledTool,
)


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


def agent_enabled_personal_server_ids(agent: PersistentAgent) -> List[str]:
    """Return MCP server IDs explicitly enabled for the agent (user scope)."""

    return [
        str(server_id)
        for server_id in PersistentAgentMCPServer.objects.filter(agent=agent)
        .values_list('server_config_id', flat=True)
    ]


def agent_accessible_server_configs(agent: PersistentAgent) -> List[MCPServerConfig]:
    """Collect all MCP server configs accessible to the agent."""

    filters = Q(scope=MCPServerConfig.Scope.PLATFORM)

    if agent.organization_id:
        filters |= Q(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization_id=agent.organization_id,
        )

    personal_ids = agent_enabled_personal_server_ids(agent)
    if personal_ids:
        filters |= Q(id__in=personal_ids)

    return list(
        MCPServerConfig.objects.filter(filters, is_active=True)
        .order_by('display_name', 'name')
    )


def agent_server_overview(agent: PersistentAgent) -> List[Dict[str, Any]]:
    """Return structured info about MCP servers available to an agent."""

    overview: List[Dict[str, Any]] = []

    personal_ids = set(agent_enabled_personal_server_ids(agent))

    for cfg in platform_server_configs():
        overview.append(
            _serialize_config(cfg, inherited=True, assigned=True)
        )

    if agent.organization_id:
        for cfg in organization_server_configs(agent.organization_id):
            overview.append(
                _serialize_config(cfg, inherited=True, assigned=True)
            )

    for cfg in personal_server_configs(agent.user_id):
        overview.append(
            _serialize_config(
                cfg,
                inherited=False,
                assigned=str(cfg.id) in personal_ids,
            )
        )

    return overview


def update_agent_personal_servers(agent: PersistentAgent, desired_ids: List[str]) -> None:
    """Set the personal (user-scoped) servers enabled for an agent."""

    desired_set = {str(pk) for pk in desired_ids}
    existing_set = set(agent_enabled_personal_server_ids(agent))

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
