from django.db import migrations, transaction
from django.db.models import Q
from django.utils.text import slugify


def _unique_org_name(Model, org_id, base_name):
    """
    Return a name unique within the organization scope, auto-suffixing on conflict.
    """

    candidate = slugify(base_name)[:64] or "server"
    suffix = 1
    while Model.objects.filter(scope="organization", organization_id=org_id, name=candidate).exists():
        candidate = f"{slugify(base_name)[:60]}-{suffix}"
        suffix += 1
    return candidate


def forwards(apps, schema_editor):
    MCPServerConfig = apps.get_model("api", "MCPServerConfig")
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    Assignment = apps.get_model("api", "PersistentAgentMCPServer")

    with transaction.atomic():
        # Step 1: Move personal MCP servers that are assigned to org agents into the org scope.
        personal_assignments = (
            Assignment.objects.filter(server_config__scope="user", agent__organization__isnull=False)
            .select_related("server_config", "agent")
            .order_by("server_config_id")
        )

        # Group assignments by server_id
        server_to_assignments = {}
        for assignment in personal_assignments:
            server_to_assignments.setdefault(assignment.server_config_id, []).append(assignment)

        for server_id, assignments in server_to_assignments.items():
            server = MCPServerConfig.objects.get(pk=server_id)

            # Determine the set of orgs this personal server was assigned to
            org_ids = []
            for assignment in assignments:
                if assignment.agent.organization_id:
                    org_ids.append(assignment.agent.organization_id)

            if not org_ids:
                continue

            org_ids = list(dict.fromkeys(org_ids))  # de-dup preserve order

            # Convert the original server to the first org, clone for additional orgs if needed
            original_base_name = server.name
            original_fields = {
                "display_name": server.display_name,
                "description": server.description,
                "command": server.command,
                "command_args": server.command_args,
                "url": server.url,
                "auth_method": server.auth_method,
                "prefetch_apps": server.prefetch_apps,
                "metadata": server.metadata,
                "env_json_encrypted": server.env_json_encrypted,
                "headers_json_encrypted": server.headers_json_encrypted,
                "is_active": server.is_active,
            }

            created_servers = []
            for idx, org_id in enumerate(org_ids):
                if idx == 0:
                    target_server = server
                    target_server.scope = "organization"
                    target_server.organization_id = org_id
                    target_server.user_id = None
                    target_server.name = _unique_org_name(MCPServerConfig, org_id, original_base_name)
                    target_server.save(update_fields=["scope", "organization", "user", "name"])
                else:
                    clone = MCPServerConfig(
                        scope="organization",
                        organization_id=org_id,
                        user_id=None,
                        name=_unique_org_name(MCPServerConfig, org_id, original_base_name),
                        **original_fields,
                    )
                    clone.save()
                    target_server = clone
                    created_servers.append(clone)

                # Reassign all assignments for this org (originally pointing at the personal server) to the target server
                Assignment.objects.filter(
                    server_config_id=server_id,
                    agent__organization_id=org_id,
                ).update(server_config=target_server)

                # Remove any assignments on the target server that point to agents outside the org
                Assignment.objects.filter(server_config=target_server).exclude(agent__organization_id=org_id).delete()

            # After conversion, ensure no stray personal assignments remain on the original server
            Assignment.objects.filter(server_config=server).exclude(agent__organization_id=server.organization_id).delete()

        # Step 2: Ensure every org-scoped server is explicitly assigned to all agents in that org.
        org_servers = MCPServerConfig.objects.filter(scope="organization")
        for server in org_servers:
            agent_ids = list(
                PersistentAgent.objects.filter(organization_id=server.organization_id).values_list("id", flat=True)
            )
            existing_assignments = set(
                Assignment.objects.filter(server_config=server).values_list("agent_id", flat=True)
            )
            new_assignments = [
                Assignment(agent_id=agent_id, server_config_id=server.id)
                for agent_id in agent_ids
                if agent_id not in existing_assignments
            ]
            Assignment.objects.bulk_create(new_assignments, ignore_conflicts=True)


def backwards(apps, schema_editor):
    # No-op reverse; we cannot safely revert scope changes or assignments.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0212_persistentagentcompletion_completion_type"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
