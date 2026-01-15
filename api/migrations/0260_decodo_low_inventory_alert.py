import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0259_proxy_health_failure_tracking"),
    ]

    operations = [
        migrations.CreateModel(
            name="DecodoLowInventoryAlert",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("sent_on", models.DateField(help_text="Local date when the alert was sent.")),
                (
                    "active_proxy_count",
                    models.PositiveIntegerField(
                        help_text="Active Decodo proxies available (excluding dedicated allocations).",
                    ),
                ),
                (
                    "threshold",
                    models.PositiveIntegerField(
                        help_text="Inventory threshold that triggered the alert.",
                    ),
                ),
                ("recipient_email", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-sent_on"],
            },
        ),
        migrations.AddConstraint(
            model_name="decodolowinventoryalert",
            constraint=models.UniqueConstraint(
                fields=("sent_on",),
                name="unique_decodo_low_inventory_alert_day",
            ),
        ),
    ]
