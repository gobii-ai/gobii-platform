from django.db import migrations


NATIVE_BRIGHTDATA_TOOLS = (
    "mcp_brightdata_search_engine",
    "mcp_brightdata_scrape_as_markdown",
)


def migrate_brightdata_tools_to_builtin(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    for tool_name in NATIVE_BRIGHTDATA_TOOLS:
        PersistentAgentEnabledTool.objects.filter(tool_full_name=tool_name).update(
            tool_server="builtin",
            tool_name=tool_name,
            server_config=None,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0420_alter_browseruseagent_name"),
    ]

    operations = [
        migrations.RunPython(migrate_brightdata_tools_to_builtin, noop_reverse),
    ]
