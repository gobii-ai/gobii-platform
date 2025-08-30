from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ("api", "0015_alter_browseruseagenttaskstep_result_value"),
    ]

    operations = [
        migrations.RenameField(
            model_name="browseruseagenttask",
            old_name="input_data",
            new_name="prompt",
        ),
    ] 