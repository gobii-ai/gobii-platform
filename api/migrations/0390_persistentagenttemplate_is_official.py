from django.db import migrations, models


def mark_library_handle_templates_official(apps, schema_editor):
    PersistentAgentTemplate = apps.get_model("api", "PersistentAgentTemplate")
    db_alias = schema_editor.connection.alias
    PersistentAgentTemplate.objects.using(db_alias).filter(
        public_profile__handle="library",
    ).update(is_official=True)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0389_browseruseagenttask_filespace_artifacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="is_official",
            field=models.BooleanField(
                default=False,
                help_text="Whether this template is an official Gobii template.",
            ),
        ),
        migrations.RunPython(mark_library_handle_templates_official, migrations.RunPython.noop),
    ]
