from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0361_human_input_request_expiration"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("event_name", models.CharField(db_index=True, max_length=200)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "User email",
                "verbose_name_plural": "User emails",
                "ordering": ("name", "event_name"),
            },
        ),
    ]
