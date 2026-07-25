from django.db import migrations
from django.utils import timezone


def skip_active_planning_sessions(apps, schema_editor):
    persistent_agent = apps.get_model("api", "PersistentAgent")
    human_input_request = apps.get_model("api", "PersistentAgentHumanInputRequest")
    active_agent_ids = list(
        persistent_agent.objects.filter(planning_state="planning").values_list("id", flat=True)
    )
    persistent_agent.objects.filter(id__in=active_agent_ids).update(
        planning_state="skipped",
    )
    human_input_request.objects.filter(
        agent_id__in=active_agent_ids,
        status="pending",
    ).update(
        status="cancelled",
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0436_browser_session_tickets"),
    ]

    operations = [
        migrations.RunPython(skip_active_planning_sessions, migrations.RunPython.noop),
    ]
