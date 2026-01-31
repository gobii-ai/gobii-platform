from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0276_burn_rate_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=128, unique=True)),
                ("value_text", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "System setting",
                "verbose_name_plural": "System settings",
                "ordering": ["key"],
            },
        ),
    ]
