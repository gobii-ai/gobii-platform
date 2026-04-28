from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0360_trialpromo_trialpromoredemption"),
    ]

    operations = [
        migrations.AddField(
            model_name="userattribution",
            name="li_fat_id_first",
            field=models.CharField(blank=True, max_length=256),
        ),
        migrations.AddField(
            model_name="userattribution",
            name="li_fat_id_last",
            field=models.CharField(blank=True, max_length=256),
        ),
    ]
