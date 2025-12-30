from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0241_agentfsnode_content_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentmodelendpoint",
            name="openrouter_preset",
            field=models.CharField(
                blank=True,
                max_length=128,
                help_text="Optional OpenRouter preset identifier applied to this endpoint.",
            ),
        ),
    ]
