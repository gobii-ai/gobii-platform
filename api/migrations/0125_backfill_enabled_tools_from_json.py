from django.db import migrations
from django.utils import timezone
from datetime import datetime
import math


def backfill_enabled_tools(apps, schema_editor):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    Enabled = apps.get_model("api", "PersistentAgentEnabledTool")

    now = timezone.now()

    # Iterate in chunks to avoid large transactions
    for agent in PersistentAgent.objects.all().iterator():
        # Both fields existed prior to this migration
        enabled_list = list(getattr(agent, "enabled_mcp_tools", []) or [])
        usage_map = dict(getattr(agent, "mcp_tool_usage", {}) or {})

        # Create rows for each enabled tool
        created = []
        for name in enabled_list:
            last_used = usage_map.get(name)
            last_used_dt = None
            if last_used is not None:
                try:
                    # Allow float or int epoch seconds
                    secs = float(last_used)
                    if math.isfinite(secs):
                        last_used_dt = datetime.fromtimestamp(secs, tz=timezone.utc)
                except Exception:
                    last_used_dt = None

            Enabled.objects.update_or_create(
                agent_id=agent.id,
                tool_full_name=name,
                defaults={
                    "enabled_at": now,  # best effort; original not stored
                    "last_used_at": last_used_dt,
                    "usage_count": 1 if last_used_dt else 0,
                },
            )
            created.append(name)

        # Enforce cap of 20 per agent using LRU (oldest last_used/ enabled)
        qs = Enabled.objects.filter(agent_id=agent.id).order_by(
            "last_used_at", "enabled_at"
        )
        excess = qs.count() - 20
        if excess > 0:
            ids = list(qs.values_list("id", flat=True)[:excess])
            Enabled.objects.filter(id__in=ids).delete()


def noop_reverse(apps, schema_editor):
    # No reverse backfill
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0124_persistent_agent_enabled_tool"),
    ]

    operations = [
        migrations.RunPython(backfill_enabled_tools, noop_reverse),
    ]

