from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0338_add_start_trial_capi_decision_override_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="evalsuiterun",
            name="launch_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="evalsuiterun",
            name="launcher_type",
            field=models.CharField(
                choices=[("suite", "Suite"), ("global_skill", "Global Skill")],
                default="suite",
                max_length=24,
            ),
        ),
    ]