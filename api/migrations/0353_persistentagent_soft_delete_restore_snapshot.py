from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0352_remove_promptconfig_browser_task_unified_history_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="soft_delete_restore_snapshot",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Snapshot of endpoint and peer-link ownership released during soft delete.",
            ),
        ),
    ]
