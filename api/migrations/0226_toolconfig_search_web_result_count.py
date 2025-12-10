from django.db import migrations, models

import api.services.tool_settings


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0225_remove_persistentagentstep_pa_step_agent_ts_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="toolconfig",
            name="search_web_result_count",
            field=models.PositiveIntegerField(
                default=api.services.tool_settings.DEFAULT_SEARCH_WEB_RESULT_COUNT,
                help_text="Preferred number of results to return from search_web (Exa).",
            ),
        ),
    ]
