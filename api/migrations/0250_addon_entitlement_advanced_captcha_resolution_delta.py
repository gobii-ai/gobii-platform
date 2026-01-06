from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0249_email_oauth_models"),
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
