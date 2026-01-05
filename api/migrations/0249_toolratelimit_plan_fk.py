from django.db import migrations, models
import django.db.models.deletion


def _backfill_toolratelimit_plan_ids(apps, schema_editor) -> None:
    ToolRateLimit = apps.get_model("api", "ToolRateLimit")
    ToolConfig = apps.get_model("api", "ToolConfig")

    plan_map = {
        plan_name: plan_id
        for plan_name, plan_id in ToolConfig.objects.exclude(plan_name__isnull=True).values_list("plan_name", "id")
    }

    missing: set[str] = set()
    for rate in ToolRateLimit.objects.all().iterator():
        plan_name = rate.plan_id
        new_id = plan_map.get(plan_name)
        if new_id is None:
            missing.add(str(plan_name))
            continue
        rate.plan_new_id = new_id
        rate.save(update_fields=["plan_new"])

    if missing:
        raise ValueError(
            "Missing ToolConfig rows for ToolRateLimit plan_name values: %s"
            % ", ".join(sorted(missing))
        )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0248_seed_plan_versions"),
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
