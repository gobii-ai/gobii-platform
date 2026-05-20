from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0383_sms_contact_purpose"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="sms_disabled",
            field=models.BooleanField(
                default=False,
                help_text="Disable outbound SMS for this agent without detaching its SMS number or message history.",
            ),
        ),
    ]
