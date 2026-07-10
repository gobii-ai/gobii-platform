from django.db import migrations, models


SETUP_TASK_NAMES = (
    "send_message",
    "instruct_agent",
    "trigger_scheduled_run",
    "dismiss_plain_request",
)
DIAGNOSTIC_TASK_NAMES = ("verify_plan_policy",)


def backfill_task_scoring_roles(apps, schema_editor):
    EvalRunTask = apps.get_model("api", "EvalRunTask")
    EvalRunTask.objects.filter(
        models.Q(name__in=SETUP_TASK_NAMES)
        | models.Q(name__startswith="inject_")
        | models.Q(name__startswith="seed_")
    ).update(is_scored=False, is_setup=True)
    EvalRunTask.objects.filter(name__in=DIAGNOSTIC_TASK_NAMES).update(
        is_scored=False,
        is_setup=False,
    )


def reverse_task_scoring_roles(apps, schema_editor):
    EvalRunTask = apps.get_model("api", "EvalRunTask")
    EvalRunTask.objects.update(is_scored=True, is_setup=False)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0418_agent_owner_category_profile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evalrun",
            name="scenario_fingerprint",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Hash of scenario data and shared eval behavior for comparability tracking.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="is_scored",
            field=models.BooleanField(
                default=True,
                help_text="Whether this requirement contributes to the scenario outcome.",
            ),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="is_setup",
            field=models.BooleanField(
                default=False,
                help_text="Whether this row records scenario setup rather than evaluated behavior.",
            ),
        ),
        migrations.RunPython(
            backfill_task_scoring_roles,
            reverse_code=reverse_task_scoring_roles,
        ),
    ]
