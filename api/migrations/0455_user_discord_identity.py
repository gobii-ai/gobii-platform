import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0454_default_new_workspaces_to_automatic_email"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserDiscordIdentity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("discord_user_id", models.CharField(max_length=32, unique=True)),
                ("username", models.CharField(max_length=255)),
                ("global_name", models.CharField(blank=True, max_length=255)),
                ("verified_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="discord_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
