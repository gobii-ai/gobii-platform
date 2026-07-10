from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0419_persistentagent_mini_description_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="browseruseagent",
            name="name",
            field=models.CharField(max_length=255),
        ),
    ]
