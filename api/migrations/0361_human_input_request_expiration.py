from datetime import timedelta

from django.db import migrations, models, transaction
from django.utils import timezone


EXPIRATION_DELTA = timedelta(days=3)


def backfill_human_input_request_expiration(apps, schema_editor):
    HumanInputRequest = apps.get_model("api", "PersistentAgentHumanInputRequest")
    now = timezone.now()
    db_alias = schema_editor.connection.alias

    with transaction.atomic(using=db_alias):
        for request in HumanInputRequest.objects.using(db_alias).filter(status="pending").iterator(chunk_size=1000):
            expires_at = (request.created_at or now) + EXPIRATION_DELTA
            request.expires_at = expires_at
            update_fields = ["expires_at"]
            if expires_at <= now:
                request.status = "expired"
                update_fields.append("status")
            request.save(update_fields=update_fields, using=db_alias)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0360_trialpromo_trialpromoredemption"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenthumaninputrequest",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_human_input_request_expiration, noop),
    ]
