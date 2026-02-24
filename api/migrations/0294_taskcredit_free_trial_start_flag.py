from django.db import migrations, models


def backfill_free_trial_start_flag(apps, _schema_editor):
    TaskCredit = apps.get_model("api", "TaskCredit")
    (
        TaskCredit.objects.filter(
            grant_type="Plan",
            additional_task=False,
            stripe_invoice_id__startswith="trial:",
        )
        .exclude(stripe_invoice_id__startswith="trial-topoff:")
        .update(free_trial_start=True)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0293_persistentagentskill"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskcredit",
            name="free_trial_start",
            field=models.BooleanField(
                default=False,
                help_text="Whether this credit grant was issued to start a free trial",
            ),
        ),
        migrations.RunPython(backfill_free_trial_start_flag, migrations.RunPython.noop),
    ]
