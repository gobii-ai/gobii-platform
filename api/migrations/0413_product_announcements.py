import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0412_alter_persistentagentsystemmessage_body_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductAnnouncement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=160)),
                ("body", models.TextField()),
                ("action_label", models.CharField(blank=True, max_length=80)),
                ("action_url", models.CharField(blank=True, max_length=500)),
                ("is_active", models.BooleanField(default=True)),
                ("published_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_product_announcements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-published_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ProductAnnouncementRead",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("read_at", models.DateTimeField()),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="read_receipts",
                        to="api.productannouncement",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_announcement_reads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="productannouncement",
            index=models.Index(fields=["is_active", "published_at"], name="prod_ann_active_pub_idx"),
        ),
        migrations.AddIndex(
            model_name="productannouncement",
            index=models.Index(fields=["expires_at"], name="prod_ann_expires_idx"),
        ),
        migrations.AddIndex(
            model_name="productannouncementread",
            index=models.Index(fields=["user", "announcement"], name="prod_ann_read_user_ann_idx"),
        ),
        migrations.AddConstraint(
            model_name="productannouncementread",
            constraint=models.UniqueConstraint(
                fields=("announcement", "user"),
                name="uniq_product_announcement_read_user",
            ),
        ),
    ]
