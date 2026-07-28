from django.db import migrations


FOLLOWUP_DIRECTIVE_PREFIX = "Unread web chat follow-up"


def deactivate_pending_followup_directives(apps, schema_editor):
    persistent_agent_system_message = apps.get_model(
        "api",
        "PersistentAgentSystemMessage",
    )
    persistent_agent_system_message.objects.filter(
        body__startswith=FOLLOWUP_DIRECTIVE_PREFIX,
        delivered_at__isnull=True,
        is_active=True,
    ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0440_merge_20260727_1716"),
    ]

    operations = [
        migrations.RunPython(
            deactivate_pending_followup_directives,
            migrations.RunPython.noop,
        ),
    ]
