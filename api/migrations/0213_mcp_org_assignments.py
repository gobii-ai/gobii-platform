from django.db import migrations, transaction


def forwards(apps, schema_editor):
    MCPServerConfig = apps.get_model("api", "MCPServerConfig")
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    Assignment = apps.get_model("api", "PersistentAgentMCPServer")

    with transaction.atomic():
        org_servers = MCPServerConfig.objects.filter(scope="organization")
        for server in org_servers:
            # If this server already has explicit assignments, leave them as-is.
            if Assignment.objects.filter(server_config=server).exists():
                continue

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
