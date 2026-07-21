from django.db import migrations


DISCORD_SKILL_KEY = "discord_native"
REACTION_TOOL_NAME = "add_discord_reaction"


def enable_discord_reaction_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    PersistentAgentSystemSkillState = apps.get_model("api", "PersistentAgentSystemSkillState")

    agent_ids = PersistentAgentSystemSkillState.objects.filter(
        skill_key=DISCORD_SKILL_KEY,
        is_enabled=True,
    ).values_list("agent_id", flat=True)
    PersistentAgentEnabledTool.objects.bulk_create(
        [
            PersistentAgentEnabledTool(
                agent_id=agent_id,
                tool_full_name=REACTION_TOOL_NAME,
                tool_server="builtin",
                tool_name=REACTION_TOOL_NAME,
            )
            for agent_id in agent_ids
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0430_persistentagentlinkreference"),
    ]

    operations = [
        migrations.RunPython(enable_discord_reaction_tool, migrations.RunPython.noop),
    ]
