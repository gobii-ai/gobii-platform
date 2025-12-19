from django.db import migrations, models

import api.services.tool_settings


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0235_alter_taskcredit_grant_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="toolconfig",
            name="brightdata_amazon_product_search_limit",
            field=models.PositiveIntegerField(
                default=api.services.tool_settings.DEFAULT_BRIGHTDATA_AMAZON_PRODUCT_SEARCH_LIMIT,
                help_text="Maximum number of results to keep from Bright Data amazon product search.",
            ),
        ),
    ]
