import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0398_rename_file_str_replace_to_apply_patch"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Organization that owns this private template, when scoped to an organization.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="agent_templates",
                to="api.organization",
            ),
        ),
        migrations.AddConstraint(
            model_name="persistentagenttemplate",
            constraint=models.CheckConstraint(
                condition=~(
                    models.Q(("public_profile__isnull", False))
                    & models.Q(("organization__isnull", False))
                ),
                name="persistent_agent_template_single_scope",
            ),
        ),
        migrations.AddIndex(
            model_name="persistentagenttemplate",
            index=models.Index(
                fields=["organization", "is_active"],
                name="pa_template_org_active_idx",
            ),
        ),
    ]
