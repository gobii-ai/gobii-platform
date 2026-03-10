from django.db import migrations, models


def backfill_image_generation_tier_use_case(apps, schema_editor):
    ImageGenerationLLMTier = apps.get_model("api", "ImageGenerationLLMTier")
    ImageGenerationLLMTier.objects.filter(use_case="").update(use_case="create_image")
    ImageGenerationLLMTier.objects.filter(use_case__isnull=True).update(use_case="create_image")


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0309_backfill_default_agent_email_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="imagegenerationllmtier",
            name="use_case",
            field=models.CharField(
                choices=[("create_image", "Create Image"), ("avatar", "Avatar")],
                default="create_image",
                help_text="Which image-generation workflow this tier ordering applies to.",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_image_generation_tier_use_case, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="imagegenerationllmtier",
            name="order",
            field=models.PositiveIntegerField(
                help_text="1-based order within the selected image generation workflow."
            ),
        ),
        migrations.AlterModelOptions(
            name="imagegenerationllmtier",
            options={"ordering": ["use_case", "order"]},
        ),
        migrations.AddConstraint(
            model_name="imagegenerationllmtier",
            constraint=models.UniqueConstraint(
                fields=("use_case", "order"),
                name="unique_image_generation_tier_order_per_use_case",
            ),
        ),
    ]
