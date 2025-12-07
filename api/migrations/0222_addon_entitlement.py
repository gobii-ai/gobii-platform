import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0221_browser_extraction_endpoint"),
    ]

    operations = [
        migrations.CreateModel(
            name="AddonEntitlement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_id", models.CharField(blank=True, default="", max_length=255)),
                ("price_id", models.CharField(max_length=255)),
                ("quantity", models.PositiveIntegerField(default=1)),
                (
                    "task_credits_delta",
                    models.IntegerField(
                        default=0,
                        help_text="Per-unit additional task credits granted for the billing cycle.",
                    ),
                ),
                (
                    "contact_cap_delta",
                    models.PositiveIntegerField(
                        default=0, help_text="Per-unit increase to max contacts per agent."
                    ),
                ),
                ("starts_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_recurring", models.BooleanField(default=False)),
                ("created_via", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="addon_entitlements",
                        to="api.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="addon_entitlements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Add-on entitlement",
                "verbose_name_plural": "Add-on entitlements",
            },
        ),
        migrations.AddConstraint(
            model_name="addonentitlement",
            constraint=models.CheckConstraint(
                check=(
                    (
                        models.Q(("user__isnull", False), ("organization__isnull", True))
                        | models.Q(("user__isnull", True), ("organization__isnull", False))
                    )
                ),
                name="addon_entitlement_owner_present",
            ),
        ),
    ]
