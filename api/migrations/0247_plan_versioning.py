from django.db import migrations, models
import django.db.models.deletion
import uuid


def _drop_toolratelimit_plan_fk(apps, schema_editor) -> None:
    ToolRateLimit = apps.get_model("api", "ToolRateLimit")
    table = ToolRateLimit._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, table)
    for name, constraint in constraints.items():
        if not constraint.get("foreign_key"):
            continue
        fk_table, _fk_column = constraint["foreign_key"]
        if fk_table != "api_toolconfig":
            continue
        if "plan_id" not in (constraint.get("columns") or []):
            continue
        schema_editor.execute(
            "ALTER TABLE %s DROP CONSTRAINT %s"
            % (
                schema_editor.quote_name(table),
                schema_editor.quote_name(name),
            )
        )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0246_add_google_docs_to_pipedream_prefetch"),
    ]

    operations = [
        migrations.RunPython(_drop_toolratelimit_plan_fk, migrations.RunPython.noop),
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                (
                    "slug",
                    models.CharField(
                        max_length=64,
                        unique=True,
                        help_text="Stable plan identifier used across versions (e.g., free, startup).",
                    ),
                ),
                (
                    "is_org",
                    models.BooleanField(default=False, help_text="Whether this plan is for organizations."),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, help_text="Whether this plan is available for use."),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["slug"],
                "verbose_name": "Plan",
                "verbose_name_plural": "Plans",
            },
        ),
        migrations.CreateModel(
            name="PlanVersion",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                (
                    "version_code",
                    models.CharField(
                        max_length=64,
                        help_text="Version code unique per plan (e.g., v1, 2024-10).",
                    ),
                ),
                (
                    "legacy_plan_code",
                    models.CharField(
                        max_length=64,
                        null=True,
                        blank=True,
                        unique=True,
                        help_text="Legacy plan identifier (e.g., pln_l_m_v1).",
                    ),
                ),
                (
                    "is_active_for_new_subs",
                    models.BooleanField(
                        default=False,
                        help_text="Whether this version is selectable for new subscriptions.",
                    ),
                ),
                ("display_name", models.CharField(max_length=128)),
                ("tagline", models.CharField(max_length=255, blank=True, default="")),
                ("description", models.TextField(blank=True, default="")),
                ("marketing_features", models.JSONField(default=list, blank=True)),
                ("effective_start_at", models.DateTimeField(null=True, blank=True)),
                ("effective_end_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="versions",
                        to="api.plan",
                    ),
                ),
            ],
            options={
                "ordering": ["plan__slug", "-created_at"],
                "verbose_name": "Plan version",
                "verbose_name_plural": "Plan versions",
            },
        ),
        migrations.CreateModel(
            name="PlanVersionPrice",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        max_length=32,
                        choices=[
                            ("base", "Base"),
                            ("seat", "Seat"),
                            ("overage", "Overage"),
                            ("task_pack", "Task pack"),
                            ("contact_pack", "Contact pack"),
                            ("browser_task_limit", "Browser task limit"),
                            ("dedicated_ip", "Dedicated IP"),
                        ],
                    ),
                ),
                (
                    "billing_interval",
                    models.CharField(
                        max_length=8,
                        choices=[("month", "Monthly"), ("year", "Yearly")],
                        null=True,
                        blank=True,
                        help_text="Billing interval for recurring prices; null for metered/add-ons.",
                    ),
                ),
                ("price_id", models.CharField(max_length=255, unique=True)),
                ("product_id", models.CharField(max_length=255, blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "plan_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="prices",
                        to="api.planversion",
                    ),
                ),
            ],
            options={
                "ordering": ["plan_version", "kind", "price_id"],
                "verbose_name": "Plan version price",
                "verbose_name_plural": "Plan version prices",
                "indexes": [
                    models.Index(fields=["price_id"]),
                    models.Index(fields=["product_id"]),
                ],
            },
        ),
        migrations.CreateModel(
            name="EntitlementDefinition",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("key", models.CharField(max_length=128, unique=True)),
                ("display_name", models.CharField(max_length=128)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "value_type",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("int", "Integer"),
                            ("decimal", "Decimal"),
                            ("bool", "Boolean"),
                            ("text", "Text"),
                            ("json", "JSON"),
                        ],
                    ),
                ),
                ("unit", models.CharField(max_length=64, blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["key"],
                "verbose_name": "Entitlement definition",
                "verbose_name_plural": "Entitlement definitions",
            },
        ),
        migrations.CreateModel(
            name="PlanVersionEntitlement",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("value_int", models.IntegerField(null=True, blank=True)),
                (
                    "value_decimal",
                    models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True),
                ),
                ("value_bool", models.BooleanField(null=True, blank=True)),
                ("value_text", models.TextField(null=True, blank=True)),
                ("value_json", models.JSONField(null=True, blank=True)),
                ("currency", models.CharField(max_length=16, null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "entitlement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plan_values",
                        to="api.entitlementdefinition",
                    ),
                ),
                (
                    "plan_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entitlements",
                        to="api.planversion",
                    ),
                ),
            ],
            options={
                "ordering": ["plan_version", "entitlement__key"],
                "verbose_name": "Plan version entitlement",
                "verbose_name_plural": "Plan version entitlements",
            },
        ),
        migrations.AddField(
            model_name="userbilling",
            name="plan_version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="user_billings",
                null=True,
                blank=True,
                help_text="Resolved plan version for this billing record.",
                to="api.planversion",
            ),
        ),
        migrations.AddField(
            model_name="organizationbilling",
            name="plan_version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="organization_billings",
                null=True,
                blank=True,
                help_text="Resolved plan version for this billing record.",
                to="api.planversion",
            ),
        ),
        migrations.AlterField(
            model_name="dailycreditconfig",
            name="plan_name",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("free", "Free"),
                    ("startup", "Startup"),
                    ("pln_l_m_v1", "Scale"),
                    ("org_team", "Team"),
                ],
                null=True,
                blank=True,
                help_text="Legacy plan identifier the daily credit pacing settings apply to.",
            ),
        ),
        migrations.AddField(
            model_name="dailycreditconfig",
            name="id",
            field=models.BigAutoField(primary_key=True, serialize=False),
        ),
        migrations.AddField(
            model_name="dailycreditconfig",
            name="plan_version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="daily_credit_configs",
                null=True,
                blank=True,
                help_text="Plan version the daily credit pacing settings apply to.",
                to="api.planversion",
            ),
        ),
        migrations.AlterField(
            model_name="browserconfig",
            name="plan_name",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("free", "Free"),
                    ("startup", "Startup"),
                    ("pln_l_m_v1", "Scale"),
                    ("org_team", "Team"),
                ],
                null=True,
                blank=True,
                help_text="Legacy plan identifier the browser limits apply to.",
            ),
        ),
        migrations.AddField(
            model_name="browserconfig",
            name="id",
            field=models.BigAutoField(primary_key=True, serialize=False),
        ),
        migrations.AddField(
            model_name="browserconfig",
            name="plan_version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="browser_configs",
                null=True,
                blank=True,
                help_text="Plan version the browser limits apply to.",
                to="api.planversion",
            ),
        ),
        migrations.AlterField(
            model_name="toolconfig",
            name="plan_name",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("free", "Free"),
                    ("startup", "Startup"),
                    ("pln_l_m_v1", "Scale"),
                    ("org_team", "Team"),
                ],
                null=True,
                blank=True,
                help_text="Legacy plan identifier the tool configuration applies to.",
            ),
        ),
        migrations.AddField(
            model_name="toolconfig",
            name="id",
            field=models.BigAutoField(primary_key=True, serialize=False),
        ),
        migrations.AddField(
            model_name="toolconfig",
            name="plan_version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tool_configs",
                null=True,
                blank=True,
                help_text="Plan version the tool configuration applies to.",
                to="api.planversion",
            ),
        ),
        migrations.AddConstraint(
            model_name="planversion",
            constraint=models.UniqueConstraint(
                fields=("plan", "version_code"),
                name="unique_plan_version_code",
            ),
        ),
        migrations.AddConstraint(
            model_name="planversion",
            constraint=models.UniqueConstraint(
                fields=("plan",),
                condition=models.Q(is_active_for_new_subs=True),
                name="unique_active_plan_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="planversionentitlement",
            constraint=models.UniqueConstraint(
                fields=("plan_version", "entitlement"),
                name="unique_plan_version_entitlement",
            ),
        ),
        migrations.AddConstraint(
            model_name="dailycreditconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_version",),
                condition=models.Q(plan_version__isnull=False),
                name="unique_daily_credit_plan_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="dailycreditconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_name",),
                condition=models.Q(plan_name__isnull=False),
                name="unique_daily_credit_plan_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="browserconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_version",),
                condition=models.Q(plan_version__isnull=False),
                name="unique_browser_config_plan_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="browserconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_name",),
                condition=models.Q(plan_name__isnull=False),
                name="unique_browser_config_plan_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="toolconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_version",),
                condition=models.Q(plan_version__isnull=False),
                name="unique_tool_config_plan_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="toolconfig",
            constraint=models.UniqueConstraint(
                fields=("plan_name",),
                condition=models.Q(plan_name__isnull=False),
                name="unique_tool_config_plan_name",
            ),
        ),
    ]
