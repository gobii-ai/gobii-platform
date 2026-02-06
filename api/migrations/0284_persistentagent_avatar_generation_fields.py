import hashlib

from django.db import migrations, models


def _compute_charter_hash(charter: str) -> str:
    normalized = (charter or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def backfill_avatar_charter_hash(apps, schema_editor):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    agents = (
        PersistentAgent.objects
        .exclude(avatar="")
        .exclude(avatar__isnull=True)
        .filter(avatar_charter_hash="")
        .only("id", "charter", "avatar_charter_hash")
    )

    for agent in agents.iterator(chunk_size=200):
        charter_hash = _compute_charter_hash(getattr(agent, "charter", ""))
        PersistentAgent.objects.filter(id=agent.id).update(avatar_charter_hash=charter_hash)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0283_imagegenerationmodelendpoint_supports_image_to_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="avatar_charter_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA256 of the charter used to generate or intentionally clear the current avatar state.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="avatar_requested_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA256 of the charter currently pending avatar generation.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="visual_description",
            field=models.TextField(
                blank=True,
                help_text="Generated detailed visual identity description used to render authentic avatar portraits.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="visual_description_charter_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA256 of the charter used to generate visual_description.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="visual_description_requested_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA256 of the charter currently pending visual description generation.",
                max_length=64,
            ),
        ),
        migrations.RunPython(backfill_avatar_charter_hash, migrations.RunPython.noop),
    ]
