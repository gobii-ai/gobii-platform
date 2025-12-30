from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0242_persistentmodelendpoint_openrouter_preset"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentmodelendpoint",
            name="max_input_tokens",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional override for the model's max input/context tokens.",
                null=True,
            ),
        ),
    ]
