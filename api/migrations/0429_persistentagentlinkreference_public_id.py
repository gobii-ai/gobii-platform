import secrets

from django.db import migrations, models

import api.models


ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _generate_public_id():
    value = secrets.randbits(80)
    encoded = []
    for _ in range(16):
        value, index = divmod(value, len(ALPHABET))
        encoded.append(ALPHABET[index])
    return f"L{''.join(reversed(encoded))}"


def populate_public_ids(apps, schema_editor):
    LinkReference = apps.get_model("api", "PersistentAgentLinkReference")
    assigned = set(LinkReference.objects.exclude(public_id__isnull=True).values_list("public_id", flat=True))
    for reference in LinkReference.objects.filter(public_id__isnull=True).iterator():
        public_id = _generate_public_id()
        while public_id in assigned:
            public_id = _generate_public_id()
        reference.public_id = public_id
        reference.save(update_fields=["public_id"])
        assigned.add(public_id)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0428_persistentagentlinkreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentlinkreference",
            name="public_id",
            field=models.CharField(editable=False, max_length=17, null=True),
        ),
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="persistentagentlinkreference",
            name="public_id",
            field=models.CharField(
                default=api.models.generate_link_reference_public_id,
                editable=False,
                max_length=17,
                unique=True,
            ),
        ),
    ]
