from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0236_toolconfig_brightdata_amazon_product_search_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="addonentitlement",
            name="browser_task_daily_delta",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Per-unit increase to per-agent daily browser task limit.",
            ),
        ),
    ]
