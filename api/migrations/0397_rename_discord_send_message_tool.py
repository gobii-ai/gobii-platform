from django.db import migrations
from django.db.models import Subquery


OLD_TOOL_NAME = "discord_send_message"
NEW_TOOL_NAME = "send_discord_message"


def rename_enabled_discord_send_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    _rename_enabled_tool_rows(PersistentAgentEnabledTool, OLD_TOOL_NAME, NEW_TOOL_NAME)


def reverse_rename_enabled_discord_send_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    _rename_enabled_tool_rows(PersistentAgentEnabledTool, NEW_TOOL_NAME, OLD_TOOL_NAME)


def _rename_enabled_tool_rows(PersistentAgentEnabledTool, old_tool_name, new_tool_name):
    already_enabled_agent_ids = (
        PersistentAgentEnabledTool.objects
        .filter(tool_full_name=new_tool_name)
        .values("agent_id")
    )
    PersistentAgentEnabledTool.objects.filter(
        tool_full_name=old_tool_name,
        agent_id__in=Subquery(already_enabled_agent_ids),
    ).delete()
    PersistentAgentEnabledTool.objects.filter(tool_full_name=old_tool_name).update(
        tool_full_name=new_tool_name
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0396_persistentagenttoolcall_parent_tool_call"),
    ]

    operations = [
        migrations.RunPython(
            rename_enabled_discord_send_tool,
            reverse_rename_enabled_discord_send_tool,
        ),
    ]
