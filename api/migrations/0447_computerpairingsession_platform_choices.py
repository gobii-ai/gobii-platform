from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0446_computer_cpp_integration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="computerpairingsession",
            name="platform",
            field=models.CharField(
                choices=[("macos", "macOS"), ("windows", "Windows")],
                max_length=32,
            ),
        ),
    ]
