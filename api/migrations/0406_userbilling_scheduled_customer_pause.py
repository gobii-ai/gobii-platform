from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0405_update_brightdata_mcp_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="userbilling",
            name="scheduled_customer_pause_effective_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When a customer-requested account pause should begin after the paid period ends.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="userbilling",
            name="scheduled_customer_pause_resume_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When a scheduled customer-requested account pause should resume.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="userbilling",
            name="scheduled_customer_pause_subscription_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Stripe subscription ID for the pending customer-requested account pause.",
                max_length=255,
            ),
        ),
    ]
