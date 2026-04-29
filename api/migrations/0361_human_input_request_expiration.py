from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


EXPIRATION_DELTA = timedelta(days=3)


def backfill_human_input_request_expiration(apps, schema_editor):
    HumanInputRequest = apps.get_model("api", "PersistentAgentHumanInputRequest")
    now = timezone.now()

    for request in HumanInputRequest.objects.filter(status="pending").iterator(chunk_size=1000):
        expires_at = (request.created_at or now) + EXPIRATION_DELTA
        request.expires_at = expires_at
        update_fields = ["expires_at"]
        if expires_at <= now:
            request.status = "expired"
            update_fields.append("status")
        request.save(update_fields=update_fields)


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
