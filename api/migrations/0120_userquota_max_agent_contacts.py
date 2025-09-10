from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0119_merge_20250910_1820"),
    ]

    operations = [
        migrations.AddField(
            model_name="userquota",
            name="max_agent_contacts",
            field=models.PositiveIntegerField(null=True, blank=True, default=None,
                                              help_text="If set (>0), overrides plan max contacts per agent for this user"),
        ),
    ]

