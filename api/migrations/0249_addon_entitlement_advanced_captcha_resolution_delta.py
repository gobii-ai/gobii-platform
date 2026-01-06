from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0248_alter_promptconfig_premium_tool_call_history_limit_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="addonentitlement",
            name="advanced_captcha_resolution_delta",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Per-unit enablement of advanced CAPTCHA resolution for browser tasks.",
            ),
        ),
    ]
