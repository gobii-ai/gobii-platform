from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0384_persistentagent_sms_disabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="permanent_instructions",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Durable long-term preferences and guidance separate from the "
                    "agent's current charter."
                ),
            ),
        ),
    ]
