from django.db import migrations, models
import django.db.models.deletion


def _backfill_toolratelimit_plan_ids(apps, schema_editor) -> None:
    ToolRateLimit = apps.get_model("api", "ToolRateLimit")
    ToolConfig = apps.get_model("api", "ToolConfig")

    rate_table = schema_editor.quote_name(ToolRateLimit._meta.db_table)
    config_table = schema_editor.quote_name(ToolConfig._meta.db_table)

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT rate.plan_id
            FROM {rate_table} AS rate
            LEFT JOIN {config_table} AS config
              ON config.plan_name = rate.plan_id
            WHERE rate.plan_id IS NOT NULL
              AND config.id IS NULL
            """.format(
                rate_table=rate_table,
                config_table=config_table,
            )
        )
        missing = [row[0] for row in cursor.fetchall()]
        if missing:
            raise ValueError(
                "Missing ToolConfig rows for ToolRateLimit plan_name values: %s"
                % ", ".join(sorted(str(value) for value in missing))
            )

        if schema_editor.connection.vendor == "postgresql":
            cursor.execute(
                """
                UPDATE {rate_table} AS rate
                SET plan_new_id = config.id
                FROM {config_table} AS config
                WHERE rate.plan_id = config.plan_name
                """.format(
                    rate_table=rate_table,
                    config_table=config_table,
                )
            )
        else:
            cursor.execute(
                """
                UPDATE {rate_table}
                SET plan_new_id = (
                    SELECT config.id
                    FROM {config_table} AS config
                    WHERE config.plan_name = {rate_table}.plan_id
                )
                WHERE plan_id IS NOT NULL
                """.format(
                    rate_table=rate_table,
                    config_table=config_table,
                )
            )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0250_seed_plan_versions"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="toolratelimit",
            name="unique_tool_rate_limit_per_plan_tool",
        ),
        migrations.AddField(
            model_name="toolratelimit",
            name="plan_new",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="api.toolconfig",
                null=True,
                blank=True,
                help_text="Tool configuration the rate limit applies to.",
            ),
        ),
        migrations.RunPython(_backfill_toolratelimit_plan_ids, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="toolratelimit",
            name="plan",
        ),
        migrations.RenameField(
            model_name="toolratelimit",
            old_name="plan_new",
            new_name="plan",
        ),
        migrations.AlterField(
            model_name="toolratelimit",
            name="plan",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rate_limits",
                to="api.toolconfig",
                help_text="Tool configuration the rate limit applies to.",
            ),
        ),
        migrations.AddConstraint(
            model_name="toolratelimit",
            constraint=models.UniqueConstraint(
                fields=("plan", "tool_name"),
                name="unique_tool_rate_limit_per_plan_tool",
            ),
        ),
    ]
