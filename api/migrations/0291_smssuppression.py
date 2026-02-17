import uuid

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0290_persistentagenttemplatelike"),
    ]

    operations = [
        migrations.CreateModel(
            name="SmsSuppression",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "phone_number",
                    models.CharField(
                        max_length=32,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Phone number must be in E.164 format (e.g., +1234567890)",
                                regex="^\\+?[1-9]\\d{1,14}$",
                            )
                        ],
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        help_text="Whether outbound SMS delivery is currently blocked for this number.",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Source for the last suppression state change (keyword, webhook code, etc.).",
                        max_length=64,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["phone_number"],
            },
        ),
        migrations.AddIndex(
            model_name="smssuppression",
            index=models.Index(fields=["is_active", "phone_number"], name="sms_supp_active_num_idx"),
        ),
    ]
