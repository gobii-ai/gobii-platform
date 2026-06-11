from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0394_merge_20260609_1501"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentmodelendpoint",
            name="litellm_pricing_model",
            field=models.CharField(
                blank=True,
                help_text="Optional LiteLLM model identifier used for pricing lookup only.",
                max_length=256,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="embeddingsmodelendpoint",
            name="litellm_pricing_model",
            field=models.CharField(
                blank=True,
                help_text="Optional LiteLLM model identifier used for pricing lookup only.",
                max_length=256,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="filehandlermodelendpoint",
            name="litellm_pricing_model",
            field=models.CharField(
                blank=True,
                help_text="Optional LiteLLM model identifier used for pricing lookup only.",
                max_length=256,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="imagegenerationmodelendpoint",
            name="litellm_pricing_model",
            field=models.CharField(
                blank=True,
                help_text="Optional LiteLLM model identifier used for pricing lookup only.",
                max_length=256,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="videogenerationmodelendpoint",
            name="litellm_pricing_model",
            field=models.CharField(
                blank=True,
                help_text="Optional LiteLLM model identifier used for pricing lookup only.",
                max_length=256,
                null=True,
            ),
        ),
    ]
