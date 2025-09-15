from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0125_backfill_enabled_tools_from_json"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="persistentagent",
            name="enabled_mcp_tools",
        ),
        migrations.RemoveField(
            model_name="persistentagent",
            name="mcp_tool_usage",
        ),
    ]

