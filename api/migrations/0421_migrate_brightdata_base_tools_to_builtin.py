from django.db import migrations
from django.db.models import F


NATIVE_BRIGHTDATA_TOOLS = (
    "mcp_brightdata_search_engine",
    "mcp_brightdata_scrape_as_markdown",
    "mcp_brightdata_web_data_linkedin_person_profile",
)


def migrate_brightdata_tools_to_builtin(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    PersistentAgentEnabledTool.objects.filter(tool_full_name__in=NATIVE_BRIGHTDATA_TOOLS).update(
        tool_server="builtin",
        tool_name=F("tool_full_name"),
        server_config=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0420_alter_browseruseagent_name"),
    ]

    operations = [
        migrations.RunPython(migrate_brightdata_tools_to_builtin, migrations.RunPython.noop),
    ]
