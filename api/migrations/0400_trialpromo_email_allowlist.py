# Generated manually after test settings disabled makemigrations.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0399_persistentagenttemplate_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="trialpromo",
            name="email_allowlist_enabled",
            field=models.BooleanField(
                default=False,
                help_text="When enabled, only users whose email is on this promo's allowlist can redeem it.",
            ),
        ),
        migrations.CreateModel(
            name="TrialPromoAllowedEmail",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("normalized_email", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "promo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allowed_emails",
                        to="api.trialpromo",
                    ),
                ),
            ],
            options={
                "verbose_name": "Trial promo allowed email",
                "verbose_name_plural": "Trial promo allowed emails",
                "ordering": ["normalized_email"],
                "indexes": [
                    models.Index(fields=["normalized_email"], name="trialpromo_email_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("promo", "normalized_email"),
                        name="uniq_trialpromo_allowed_email",
                    ),
                ],
            },
        ),
    ]
