from django.db import migrations, models
import uuid
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0120_add_meter_batch_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="MeteringBatch",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("batch_key", models.CharField(max_length=64, unique=True, db_index=True)),
                ("idempotency_key", models.CharField(max_length=128, unique=True, db_index=True)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("total_credits", models.DecimalField(max_digits=12, decimal_places=3, default=0)),
                ("rounded_quantity", models.IntegerField(default=0)),
                ("stripe_event_id", models.CharField(max_length=128, null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="metering_batches", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="meteringbatch",
            index=models.Index(fields=["user", "created_at"], name="meter_batch_user_ts_idx"),
        ),
    ]

