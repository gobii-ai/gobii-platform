from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0405_update_brightdata_mcp_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="time_to_first_token_ms",
            field=models.IntegerField(
                null=True,
                blank=True,
                help_text="Time in milliseconds until the first streamed response chunk, when available.",
            ),
        ),
    ]
