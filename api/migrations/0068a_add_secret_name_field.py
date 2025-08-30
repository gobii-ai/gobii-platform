from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0068_auto_20250726_2107"),
    ]

    operations = [
        # Add a nullable/blank name field so we can back-fill existing rows first
        migrations.AddField(
            model_name="persistentagentsecret",
            name="name",
            field=models.CharField(
                max_length=128,
                null=True,
                blank=True,
                help_text="Human-readable name for this secret (e.g., 'X Password', 'API Key')",
            ),
        ),
    ] 