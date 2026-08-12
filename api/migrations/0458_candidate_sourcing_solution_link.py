from django.db import migrations
from django.utils import timezone


TEMPLATE_CODE = "ai-agent-for-candidate-sourcing"
SOLUTION_LINK_BLOCK = (
    "## Explore the full sourcing workflow\n\n"
    "See how Gobii turns a role brief into a source-linked, recruiter-reviewed shortlist "
    "in the [AI candidate sourcing solution](/solutions/recruiting/candidate-sourcing/)."
)


def add_candidate_sourcing_solution_link(apps, schema_editor):
    Template = apps.get_model("api", "PersistentAgentTemplate")
    template = Template.objects.filter(code=TEMPLATE_CODE).first()
    if not template:
        return

    current_markdown = str(template.description_markdown or "").strip()
    if "/solutions/recruiting/candidate-sourcing/" in current_markdown:
        return

    template.description_markdown = "\n\n".join(
        part for part in (current_markdown, SOLUTION_LINK_BLOCK) if part
    )
    template.updated_at = timezone.now()
    template.save(update_fields=["description_markdown", "updated_at"])


def remove_candidate_sourcing_solution_link(apps, schema_editor):
    Template = apps.get_model("api", "PersistentAgentTemplate")
    template = Template.objects.filter(code=TEMPLATE_CODE).first()
    if not template:
        return

    current_markdown = str(template.description_markdown or "").strip()
    if SOLUTION_LINK_BLOCK not in current_markdown:
        return

    template.description_markdown = current_markdown.replace(
        SOLUTION_LINK_BLOCK,
        "",
    ).strip()
    template.updated_at = timezone.now()
    template.save(update_fields=["description_markdown", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0457_merge_20260811_1623"),
    ]

    operations = [
        migrations.RunPython(
            add_candidate_sourcing_solution_link,
            remove_candidate_sourcing_solution_link,
        ),
    ]
