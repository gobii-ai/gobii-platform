from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0190_promptconfig"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="persistentagent",
            name="pa_proactive_opt_idx",
        ),
        migrations.AddIndex(
            model_name="persistentagent",
            index=models.Index(
                fields=[
                    "proactive_opt_in",
                    "is_active",
                    "life_state",
                    "proactive_last_trigger_at",
                    "last_interaction_at",
                    "created_at",
                ],
                name="pa_proactive_sched_idx",
            ),
        ),
    ]
