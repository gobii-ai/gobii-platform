from django.db import migrations


OLD_TOOL_NAME = "discord_send_message"
NEW_TOOL_NAME = "send_discord_message"


def rename_enabled_discord_send_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    _rename_enabled_tool_rows(PersistentAgentEnabledTool, OLD_TOOL_NAME, NEW_TOOL_NAME)


def reverse_rename_enabled_discord_send_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    _rename_enabled_tool_rows(PersistentAgentEnabledTool, NEW_TOOL_NAME, OLD_TOOL_NAME)


def _rename_enabled_tool_rows(PersistentAgentEnabledTool, old_tool_name, new_tool_name):
    old_rows = list(
        PersistentAgentEnabledTool.objects
        .filter(tool_full_name=old_tool_name)
        .only("id", "agent_id", "tool_full_name")
    )
    for old_row in old_rows:
        if PersistentAgentEnabledTool.objects.filter(
            agent_id=old_row.agent_id,
            tool_full_name=new_tool_name,
        ).exists():
            old_row.delete()
            continue

        old_row.tool_full_name = new_tool_name
        old_row.save(update_fields=["tool_full_name"])


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
