from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0388_native_integration_secret_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="browseruseagenttask",
            name="filespace_artifacts",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Filespace metadata for browser-use attachment artifacts persisted after task completion.",
            ),
        ),
    ]
