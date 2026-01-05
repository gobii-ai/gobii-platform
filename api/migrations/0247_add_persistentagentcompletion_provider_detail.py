from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0246_add_google_docs_to_pipedream_prefetch"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="llm_provider_detail",
            field=models.CharField(
                max_length=128,
                null=True,
                blank=True,
                help_text="Specific upstream provider selected by routing services (e.g., OpenRouter).",
            ),
        ),
    ]
