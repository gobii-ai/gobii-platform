from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0220_llm_tier_reasoning_override"),
    ]

    operations = [
        migrations.AddField(
            model_name="browsertierendpoint",
            name="extraction_endpoint",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional paired endpoint used for page extraction LLM calls.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="as_extraction_in_tiers",
                to="api.browsermodelendpoint",
            ),
        ),
        migrations.AddField(
            model_name="profilebrowsertierendpoint",
            name="extraction_endpoint",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional paired endpoint used for page extraction LLM calls.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="as_extraction_in_profile_tiers",
                to="api.browsermodelendpoint",
            ),
        ),
    ]

