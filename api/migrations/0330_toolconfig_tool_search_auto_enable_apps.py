from django.db import migrations, models

from api.services.tool_settings import DEFAULT_TOOL_SEARCH_AUTO_ENABLE_APPS


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0329_merge_20260326_2155"),
    ]

    operations = [
        migrations.AddField(
            model_name="toolconfig",
            name="tool_search_auto_enable_apps",
            field=models.BooleanField(
                default=DEFAULT_TOOL_SEARCH_AUTO_ENABLE_APPS,
                help_text=(
                    "Allow tool search to auto-enable matching Pipedream apps via enable_apps. "
                    "When disabled, agents are told to direct users to Add Apps instead."
                ),
            ),
        ),
    ]
