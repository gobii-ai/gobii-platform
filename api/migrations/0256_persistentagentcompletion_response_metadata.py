from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0255_plan_version_price_shared_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="response_id",
            field=models.CharField(
                max_length=256,
                null=True,
                blank=True,
                help_text="Provider response identifier when available.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="request_duration_ms",
            field=models.IntegerField(
                null=True,
                blank=True,
                help_text="Time in milliseconds spent waiting for the completion response.",
            ),
        ),
    ]
