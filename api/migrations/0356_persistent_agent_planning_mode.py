from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0355_add_cta_signup_modal_flag"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="planning_state",
            field=models.CharField(
                choices=[
                    ("planning", "Planning"),
                    ("completed", "Completed"),
                    ("skipped", "Skipped"),
                ],
                db_index=True,
                default="skipped",
                help_text="Planning lifecycle state for prompt-led agent setup.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="planning_plan",
            field=models.TextField(
                blank=True,
                help_text="Final plan captured when planning mode completes.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="planning_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Timestamp when planning mode was completed through end_planning.",
                null=True,
            ),
        ),
    ]
