from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0156_merge_20251009_1404"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentPromptArchive",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("rendered_at", models.DateTimeField(help_text="Timestamp when the prompt was rendered.")),
                ("storage_key", models.CharField(max_length=512, help_text="Object storage key for the compressed prompt payload.")),
                ("raw_bytes", models.IntegerField(help_text="Uncompressed payload size in bytes.")),
                ("compressed_bytes", models.IntegerField(help_text="Compressed payload size in bytes.")),
                ("tokens_before", models.IntegerField(help_text="Token count before prompt fitting.")),
                ("tokens_after", models.IntegerField(help_text="Token count after prompt fitting.")),
                ("tokens_saved", models.IntegerField(help_text="Tokens removed during fitting.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("agent", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="prompt_archives", to="api.persistentagent")),
                ("step", models.OneToOneField(blank=True, null=True, on_delete=models.deletion.CASCADE, related_name="llm_prompt_archive", to="api.persistentagentstep")),
            ],
            options={
                "ordering": ["-rendered_at"],
            },
        ),
        migrations.AddIndex(
            model_name="persistentagentpromptarchive",
            index=models.Index(fields=["agent", "-rendered_at"], name="pa_prompt_archive_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentpromptarchive",
            index=models.Index(fields=["rendered_at"], name="pa_prompt_archive_rendered_idx"),
        ),
    ]
