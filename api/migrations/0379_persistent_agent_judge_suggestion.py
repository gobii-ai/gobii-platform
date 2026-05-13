import uuid

import django.db.models.deletion
from django.db import migrations, models


FLAG_NAME = "persistent_agent_llm_judge"


def add_judge_flag(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    if Flag.objects.filter(name=FLAG_NAME).exists():
        return
    Flag.objects.create(
        name=FLAG_NAME,
        everyone=None,
        percent=0,
        superusers=False,
        staff=False,
        authenticated=False,
        note="Enable the advisory LLM trajectory judge for selected persistent-agent users.",
    )


def keep_judge_flag(apps, schema_editor):
    """No reverse operation; keep the flag if present."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0378_intelligencetier_blacklisted_tools"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmroutingprofile",
            name="agent_judge_endpoint",
            field=models.ForeignKey(
                blank=True,
                help_text="Endpoint used for advisory persistent-agent trajectory judge calls.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_judge_profiles",
                to="api.persistentmodelendpoint",
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
                    ("llm_judge", "LLM Judge"),
                    ("other", "Other"),
                ],
                default="orchestrator",
                help_text="Origin of the completion (orchestrator loop, compaction, tag generation, etc.).",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="persistentagentsystemstep",
            name="code",
            field=models.CharField(
                choices=[
                    ("PROCESS_EVENTS", "Process Events"),
                    ("PEER_LINK_CREATED", "Peer Link Created"),
                    ("SNAPSHOT", "Snapshot"),
                    ("CREDENTIALS_PROVIDED", "Credentials Provided"),
                    ("CONTACTS_APPROVED", "Contacts Approved"),
                    ("COLLABORATOR_ADDED", "Collaborator Added"),
                    ("LLM_CONFIGURATION_REQUIRED", "LLM Configuration Required"),
                    ("PROACTIVE_TRIGGER", "Proactive Trigger"),
                    ("SYSTEM_DIRECTIVE", "System Directive"),
                    ("LLM_JUDGE_SUGGESTION", "LLM Judge Suggestion"),
                    ("BURN_RATE_COOLDOWN", "Burn Rate Cooldown"),
                    ("RATE_LIMIT", "Rate Limit"),
                ],
                max_length=64,
            ),
        ),
        migrations.CreateModel(
            name="PersistentAgentJudgeSuggestion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "suggestion_type",
                    models.CharField(
                        choices=[
                            ("intelligence_upgrade", "Intelligence Upgrade"),
                            ("stonewall_reframe", "Stonewall Reframe"),
                            ("request_human_input", "Request Human Input"),
                            ("strategy_shift", "Strategy Shift"),
                        ],
                        max_length=64,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("ui_message", models.TextField()),
                ("agent_directive", models.TextField(blank=True)),
                ("confidence", models.FloatField(default=0)),
                ("recommended_tier", models.CharField(blank=True, max_length=64)),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("trigger_reasons", models.JSONField(blank=True, default=list)),
                ("evidence_hash", models.CharField(db_index=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("dismissed", "Dismissed")],
                        db_index=True,
                        default="active",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="judge_suggestions",
                        to="api.persistentagent",
                    ),
                ),
                (
                    "source_step",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="judge_suggestions",
                        to="api.persistentagentstep",
                    ),
                ),
                (
                    "system_message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="judge_suggestions",
                        to="api.persistentagentsystemmessage",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["agent", "status", "-created_at"], name="pa_judge_agent_status_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("agent", "suggestion_type", "evidence_hash"),
                        name="uniq_pa_judge_suggestion_evidence",
                    ),
                ],
            },
        ),
        migrations.RunPython(add_judge_flag, keep_judge_flag),
    ]
