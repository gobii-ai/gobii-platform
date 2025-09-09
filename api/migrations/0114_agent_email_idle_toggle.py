from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0113_agent_email_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentemailaccount",
            name="imap_idle_enabled",
            field=models.BooleanField(default=False),
        ),
    ]

