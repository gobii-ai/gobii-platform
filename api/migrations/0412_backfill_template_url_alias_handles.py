from django.db import migrations


LEGACY_TEMPLATE_ALIASES = (
    {
        "handle": "gentle-isle",
        "slug": "renewable-energy-market-analyst",
        "target_slug": "renewable-energy-market-analyst",
    },
)


def backfill_alias_handles(apps, schema_editor):
    Alias = apps.get_model("api", "PersistentAgentTemplateUrlAlias")

    aliases = Alias.objects.select_related("public_profile").filter(handle="")
    for alias in aliases.iterator():
        if not alias.public_profile_id:
            continue
        handle = str(alias.public_profile.handle or "").strip()
        if handle:
            Alias.objects.filter(pk=alias.pk).update(handle=handle)


def seed_known_legacy_template_aliases(apps, schema_editor):
    Template = apps.get_model("api", "PersistentAgentTemplate")
    Alias = apps.get_model("api", "PersistentAgentTemplateUrlAlias")

    for alias_data in LEGACY_TEMPLATE_ALIASES:
        template = (
            Template.objects.filter(
                slug=alias_data["target_slug"],
                organization__isnull=True,
                is_active=True,
            )
            .order_by("priority", "display_name", "id")
            .first()
        )
        if not template:
            continue

        Alias.objects.update_or_create(
            handle=alias_data["handle"],
            slug=alias_data["slug"],
            defaults={"template_id": template.id},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0411_template_url_alias_legacy_handle"),
    ]

    operations = [
        migrations.RunPython(backfill_alias_handles, migrations.RunPython.noop),
        migrations.RunPython(seed_known_legacy_template_aliases, migrations.RunPython.noop),
    ]
