from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0116_merge_20250909_0131"),
    ]

    operations = [
        migrations.AddField(
            model_name="browseruseagenttask",
            name="metered",
            field=models.BooleanField(default=False, db_index=True, help_text="Marked true once included in Stripe metering rollup."),
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="metered",
            field=models.BooleanField(default=False, db_index=True, help_text="Marked true once included in Stripe metering rollup."),
        ),
    ]

