from django.db import migrations, models


def backfill_last_visible_at(apps, schema_editor):
    PersistentAgentWebSession = apps.get_model("api", "PersistentAgentWebSession")
    PersistentAgentWebSession.objects.filter(last_visible_at__isnull=True).update(
        last_visible_at=models.F("last_seen_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0320_add_trial_ended_non_renewal_pause_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentwebsession",
            name="is_visible",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="persistentagentwebsession",
            name="last_visible_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_last_visible_at, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="persistentagentwebsession",
            index=models.Index(
                fields=["agent", "is_visible", "last_visible_at"],
                name="pa_web_session_visibility_idx",
            ),
        ),
    ]
