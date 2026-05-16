from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0379_persistent_agent_judge_suggestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="evalruntask",
            name="debug_artifacts",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Sanitized eval debugging context such as prompt snippets, tool params, "
                    "judge context, and artifact IDs."
                ),
            ),
        ),
    ]
