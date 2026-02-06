from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0282_seed_create_image_tool_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="imagegenerationmodelendpoint",
            name="supports_image_to_image",
            field=models.BooleanField(
                default=False,
                help_text="Indicates this endpoint can accept source image inputs for image-to-image edits.",
            ),
        ),
    ]
