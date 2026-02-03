from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0279_rename_service_partner_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttoolcall",
            name="status",
            field=models.CharField(
                max_length=32,
                default="complete",
                blank=True,
                help_text="Execution status for the tool call (pending, complete, error).",
            ),
        ),
    ]
