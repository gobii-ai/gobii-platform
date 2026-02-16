from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0286_intelligencetier_is_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentcomputesession",
            name="last_filespace_pull_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
