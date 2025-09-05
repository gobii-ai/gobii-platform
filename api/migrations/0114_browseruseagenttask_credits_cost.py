from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0113_fractional_taskcredit_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="browseruseagenttask",
            name="credits_cost",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True, help_text="Credits charged for this task; defaults to configured per‑task cost."),
        ),
    ]

