from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0122_persistent_endpoint_api_base"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentmodelendpoint",
            name="use_parallel_tool_calls",
            field=models.BooleanField(default=True),
        ),
    ]

