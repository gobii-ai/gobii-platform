from django.db import migrations


def populate_secret_names(apps, schema_editor):
    """Populate the `name` column for existing secrets so it is unique per (agent, domain_pattern)."""
    Secret = apps.get_model("api", "PersistentAgentSecret")

    # Fetch secrets with missing or placeholder names
    placeholders = [None, "", "Unnamed Secret"]
    for secret in Secret.objects.filter(name__in=placeholders).iterator():
        # Derive a human-readable name from the key
        base_name = (secret.key or "Unnamed Secret").replace("_", " ").title()
        base_name = base_name[:128]  # ensure max length

        # Ensure uniqueness within (agent, domain_pattern)
        existing_names = set(
            Secret.objects.filter(
                agent=secret.agent,
                domain_pattern=secret.domain_pattern
            ).exclude(pk=secret.pk).values_list("name", flat=True)
        )
        candidate = base_name
        suffix = 1
        while candidate in existing_names:
            candidate = f"{base_name} ({suffix})"[:128]
            suffix += 1

        secret.name = candidate
        secret.save(update_fields=["name"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0068a_add_secret_name_field"),
    ]

    operations = [
        migrations.RunPython(populate_secret_names, migrations.RunPython.noop),
    ] 