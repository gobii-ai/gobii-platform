from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0405_update_brightdata_mcp_version"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name="HistoricalAgentCostSample",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_sample_id", models.CharField(max_length=255, unique=True)),
                ("source_agent_id", models.UUIDField(db_index=True)),
                ("source_database", models.CharField(blank=True, max_length=128)),
                ("agent_name", models.CharField(blank=True, max_length=255)),
                ("charter_text", models.TextField(blank=True)),
                ("planning_plan", models.TextField(blank=True)),
                ("schedule_summary", models.CharField(blank=True, max_length=255)),
                ("enabled_tools", models.JSONField(blank=True, default=list)),
                ("owner_type", models.CharField(blank=True, max_length=32)),
                ("tier_key", models.CharField(blank=True, db_index=True, max_length=32)),
                (
                    "tier_credit_multiplier",
                    models.DecimalField(decimal_places=3, default=Decimal("1.000"), max_digits=8),
                ),
                ("setup_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("first_run_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("daily_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("monthly_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("normalized_setup_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                (
                    "normalized_first_run_credits",
                    models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
                ),
                ("normalized_daily_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("normalized_monthly_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                (
                    "sample_confidence",
                    models.CharField(
                        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
                        default="low",
                        max_length=16,
                    ),
                ),
                ("charged_step_count", models.PositiveIntegerField(default=0)),
                ("tool_call_count", models.PositiveIntegerField(default=0)),
                ("observation_days", models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ("embedding_text", models.TextField(blank=True)),
                ("embedding_text_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("embedding_model", models.CharField(blank=True, max_length=128)),
                ("embedding_dimension", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("source_metadata", models.JSONField(blank=True, default=dict)),
                ("created_at_source", models.DateTimeField(blank=True, null=True)),
                ("planning_completed_at_source", models.DateTimeField(blank=True, null=True)),
                ("last_observed_at_source", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-last_observed_at_source", "-created_at_source"],
            },
        ),
        migrations.CreateModel(
            name="PersistentAgentCreditForecast",
            fields=[
                (
                    "agent",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="credit_forecast",
                        serialize=False,
                        to="api.persistentagent",
                    ),
                ),
                ("setup_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("per_run_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("daily_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("monthly_credits", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                (
                    "confidence",
                    models.CharField(
                        choices=[("none", "None"), ("low", "Low"), ("medium", "Medium"), ("high", "High")],
                        default="none",
                        max_length=16,
                    ),
                ),
                ("sample_count", models.PositiveIntegerField(default=0)),
                (
                    "warning_level",
                    models.CharField(
                        choices=[("none", "None"), ("medium", "Medium"), ("high", "High")],
                        default="none",
                        max_length=16,
                    ),
                ),
                ("warning_reasons", models.JSONField(blank=True, default=list)),
                ("embedding_text", models.TextField(blank=True)),
                ("embedding_model", models.CharField(blank=True, max_length=128)),
                ("estimated_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-estimated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="historicalagentcostsample",
            index=models.Index(
                fields=["embedding_dimension", "-last_observed_at_source"],
                name="agent_cost_emb_dim_recent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="historicalagentcostsample",
            index=models.Index(
                fields=["source_database", "source_agent_id"],
                name="agent_cost_source_agent_idx",
            ),
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "api_historicalagentcostsample" ADD COLUMN "embedding_vector" vector;',
            reverse_sql='ALTER TABLE "api_historicalagentcostsample" DROP COLUMN IF EXISTS "embedding_vector";',
        ),
    ]
