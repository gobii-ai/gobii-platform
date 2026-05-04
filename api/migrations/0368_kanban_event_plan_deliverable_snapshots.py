from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0367_persistent_agent_system_skill_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentkanbanevent",
            name="snapshot_files",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="persistentagentkanbanevent",
            name="snapshot_messages",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
