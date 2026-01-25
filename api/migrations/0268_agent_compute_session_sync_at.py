from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0267_add_sandbox_compute_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentcomputesession",
            name="last_filespace_sync_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
