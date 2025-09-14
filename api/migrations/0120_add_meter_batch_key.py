from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0119_merge_20250910_1820"),
    ]

    operations = [
        migrations.AddField(
            model_name="browseruseagenttask",
            name="meter_batch_key",
            field=models.CharField(max_length=64, null=True, blank=True, db_index=True),
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="meter_batch_key",
            field=models.CharField(max_length=64, null=True, blank=True, db_index=True),
        ),
    ]

