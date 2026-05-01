from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0364_persistent_agent_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="llm_tool_names",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Tool function names included in the LLM request for this completion.",
            ),
        ),
        migrations.AlterField(
            model_name="persistentagentcompletion",
            name="completion_type",
            field=models.CharField(
                choices=[
                    ("orchestrator", "Orchestrator"),
                    ("compaction", "Comms Compaction"),
                    ("step_compaction", "Step Compaction"),
                    ("tag", "Tag Generation"),
                    ("short_description", "Short Description"),
                    ("mini_description", "Mini Description"),
                    ("avatar_visual_description", "Avatar Visual Description"),
                    ("avatar_image_generation", "Avatar Image Generation"),
                    ("image_generation", "Image Generation"),
                    ("video_generation", "Video Generation"),
                    ("tool_search", "Tool Search"),
                    ("template_clone", "Template Clone"),
                    ("agent_chat_suggestion", "Agent Chat Suggestion"),
                    ("human_input_request_matching", "Human Input Request Matching"),
                    ("other", "Other"),
                ],
                default="orchestrator",
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
            ),
        ),
    ]
