import django.db.models.deletion
from django.db import migrations, models

import api.models


def copy_source_agent_preferred_tiers(apps, schema_editor):
    PersistentAgentTemplate = apps.get_model("api", "PersistentAgentTemplate")
    db_alias = schema_editor.connection.alias
    updates = []
    templates = (
        PersistentAgentTemplate.objects.using(db_alias)
        .filter(source_agent_id__isnull=False)
        .select_related("source_agent")
    )
    for template in templates.iterator():
        preferred_llm_tier_id = getattr(template.source_agent, "preferred_llm_tier_id", None)
        if not preferred_llm_tier_id:
            continue
        template.preferred_llm_tier_id = preferred_llm_tier_id
        updates.append(template)

    if updates:
        PersistentAgentTemplate.objects.using(db_alias).bulk_update(
            updates,
            ["preferred_llm_tier"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0406_persistentagentcompletion_time_to_first_token_ms"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="preferred_llm_tier",
            field=models.ForeignKey(
                default=api.models._get_default_intelligence_tier_id,
                help_text="Preferred intelligence tier controlling LLM routing for agents launched from this template.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="preferred_by_templates",
                to="api.intelligencetier",
            ),
        ),
        migrations.RunPython(
            copy_source_agent_preferred_tiers,
            migrations.RunPython.noop,
        ),
    ]
