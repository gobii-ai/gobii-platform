from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0418_agent_owner_category_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="mini_description_mode",
            field=models.CharField(
                choices=[("auto", "Automatic"), ("manual", "Manual")],
                default="auto",
                help_text="Whether the mini description is generated from the charter or maintained manually.",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="persistentagent",
            name="mini_description",
            field=models.CharField(
                blank=True,
                help_text="Ultra-short summary of the agent for compact displays.",
                max_length=80,
            ),
        ),
    ]
