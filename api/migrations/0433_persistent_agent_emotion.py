from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0432_persistentagentschedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="emotion",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Temporary emoji the agent chose to express its current feeling.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="emotion_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the agent's temporary emotion stops being active.",
                null=True,
            ),
        ),
    ]
