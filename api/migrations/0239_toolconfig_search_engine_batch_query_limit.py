from django.db import migrations, models

import api.services.tool_settings


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0238_persistentagent_avatar"),
    ]

    operations = [
        migrations.AddField(
            model_name="toolconfig",
            name="search_engine_batch_query_limit",
            field=models.PositiveIntegerField(
                default=api.services.tool_settings.DEFAULT_SEARCH_ENGINE_BATCH_QUERY_LIMIT,
                help_text="Maximum number of queries allowed in a single search_engine_batch call.",
            ),
        ),
    ]
