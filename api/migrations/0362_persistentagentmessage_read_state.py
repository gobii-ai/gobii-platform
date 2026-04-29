import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


def backfill_existing_latest_outbound_reads(apps, schema_editor):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    PersistentAgentMessage = apps.get_model("api", "PersistentAgentMessage")
    PersistentAgentMessageRead = apps.get_model("api", "PersistentAgentMessageRead")
    AgentCollaborator = apps.get_model("api", "AgentCollaborator")
    OrganizationMembership = apps.get_model("api", "OrganizationMembership")
    db_alias = schema_editor.connection.alias

    agent_ids = (
        PersistentAgentMessage.objects.using(db_alias)
        .filter(is_outbound=True)
        .filter(Q(raw_payload__hide_in_chat=False) | Q(raw_payload__hide_in_chat__isnull=True))
        .exclude(owner_agent_id__isnull=True)
        .values_list("owner_agent_id", flat=True)
        .distinct()
    )

    batch = []
    batch_size = 1000
    now = None
    for agent in PersistentAgent.objects.using(db_alias).filter(id__in=agent_ids).iterator(chunk_size=500):
        message = (
            PersistentAgentMessage.objects.using(db_alias)
            .filter(owner_agent_id=agent.id, is_outbound=True)
            .filter(Q(raw_payload__hide_in_chat=False) | Q(raw_payload__hide_in_chat__isnull=True))
            .order_by("-timestamp", "-seq")
            .first()
        )
        if message is None:
            continue

        user_ids = set()
        if agent.user_id:
            user_ids.add(agent.user_id)
        if agent.organization_id:
            user_ids.update(
                OrganizationMembership.objects.using(db_alias)
                .filter(org_id=agent.organization_id, status="active")
                .values_list("user_id", flat=True)
            )
        user_ids.update(
            AgentCollaborator.objects.using(db_alias)
            .filter(agent_id=agent.id)
            .values_list("user_id", flat=True)
        )
        if not user_ids:
            continue

        read_at = message.timestamp
        if read_at is None:
            if now is None:
                from django.utils import timezone

                now = timezone.now()
            read_at = now

        for user_id in user_ids:
            batch.append(
                PersistentAgentMessageRead(
                    id=uuid.uuid4(),
                    message_id=message.id,
                    user_id=user_id,
                    read_at=read_at,
                    read_source="migration",
                )
            )
            if len(batch) >= batch_size:
                PersistentAgentMessageRead.objects.using(db_alias).bulk_create(batch, ignore_conflicts=True)
                batch = []

    if batch:
        PersistentAgentMessageRead.objects.using(db_alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("api", "0361_human_input_request_expiration"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersistentAgentMessageRead",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("read_at", models.DateTimeField()),
                ("read_source", models.CharField(blank=True, max_length=32)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="read_receipts",
                        to="api.persistentagentmessage",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="persistent_agent_message_reads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="persistentagentmessageread",
            constraint=models.UniqueConstraint(
                fields=("message", "user"),
                name="uniq_pa_msg_read_message_user",
            ),
        ),
        migrations.AddIndex(
            model_name="persistentagentmessageread",
            index=models.Index(fields=["user", "message"], name="pa_msg_read_user_msg_idx"),
        ),
        migrations.AddIndex(
            model_name="persistentagentmessageread",
            index=models.Index(fields=["message", "user"], name="pa_msg_read_msg_user_idx"),
        ),
        migrations.RunPython(backfill_existing_latest_outbound_reads, migrations.RunPython.noop),
    ]
