from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0299_promptconfig_browser_task_unified_history_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="userquota",
            name="max_intelligence_tier",
            field=models.CharField(
                blank=True,
                choices=[
                    ("standard", "Standard"),
                    ("premium", "Premium"),
                    ("max", "Max"),
                    ("ultra", "Ultra"),
                    ("ultra_max", "Ultra Max"),
                ],
                default=None,
                help_text="If set, this value overrides the plan's maximum intelligence tier for this user. It can be used to either raise or lower the tier limit.",
                max_length=16,
                null=True,
            ),
        ),
    ]
