import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0422_update_pretrained_employee_descriptions"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="prompt_archive",
            field=models.OneToOneField(
                blank=True,
                help_text="Archived prompt payload used for this completion, when available.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="completion",
                to="api.persistentagentpromptarchive",
            ),
        ),
    ]
