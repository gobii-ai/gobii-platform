from collections import defaultdict

from django.db import migrations, models


MAX_TEMPLATE_SLUG_LENGTH = 80


def _build_unique_slug(base_slug, used_slugs):
    base = (base_slug or "template")[:MAX_TEMPLATE_SLUG_LENGTH].strip("-") or "template"
    candidate = base
    suffix = 1

    while candidate in used_slugs:
        suffix += 1
        suffix_text = f"-{suffix}"
        max_base_length = MAX_TEMPLATE_SLUG_LENGTH - len(suffix_text)
        trimmed_base = base[:max_base_length].strip("-") or "template"
        candidate = f"{trimmed_base}{suffix_text}"

    return candidate


def dedupe_public_template_slugs(apps, schema_editor):
    Template = apps.get_model("api", "PersistentAgentTemplate")

    public_templates = list(
        Template.objects.filter(public_profile__isnull=False)
        .exclude(slug="")
        .values("id", "slug")
        .order_by("slug", "created_at", "id")
    )
    used_slugs = {template["slug"] for template in public_templates}

    templates_by_slug = defaultdict(list)
    for template in public_templates:
        templates_by_slug[template["slug"]].append(template)

    for slug in sorted(templates_by_slug):
        duplicates = templates_by_slug[slug]
        if len(duplicates) < 2:
            continue

        for duplicate in duplicates[1:]:
            new_slug = _build_unique_slug(slug, used_slugs)
            Template.objects.filter(pk=duplicate["id"]).update(slug=new_slug)
            used_slugs.add(new_slug)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0384_persistentagent_sms_disabled"),
    ]

    operations = [
        migrations.RunPython(dedupe_public_template_slugs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="persistentagenttemplate",
            constraint=models.UniqueConstraint(
                condition=models.Q(public_profile__isnull=False) & ~models.Q(slug=""),
                fields=("slug",),
                name="unique_public_template_slug",
            ),
        ),
    ]
