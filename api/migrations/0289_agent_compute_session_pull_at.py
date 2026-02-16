from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0288_merge_20260211_1743"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentcomputesession",
            name="last_filespace_pull_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
