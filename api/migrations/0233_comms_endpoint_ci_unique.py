from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0232_merge_20251215_2135"),
    ]

    def dedupe_endpoints(apps, schema_editor):
        Endpoint = apps.get_model("api", "PersistentAgentCommsEndpoint")
        Message = apps.get_model("api", "PersistentAgentMessage")
        ConversationParticipant = apps.get_model("api", "PersistentAgentConversationParticipant")
        Agent = apps.get_model("api", "PersistentAgent")
        AgentEmailAccount = apps.get_model("api", "AgentEmailAccount")
        AgentPeerLink = apps.get_model("api", "AgentPeerLink")
        conn = schema_editor.connection

        dup_groups = (
            Endpoint.objects.annotate(addr_lower=Lower("address"))
            .values("channel", "addr_lower")
            .annotate(ct=models.Count("id"))
            .filter(ct__gt=1)
        )

        def reassign(old_id, new_id):
            Message.objects.filter(from_endpoint_id=old_id).update(from_endpoint_id=new_id)
            Message.objects.filter(to_endpoint_id=old_id).update(to_endpoint_id=new_id)
            ConversationParticipant.objects.filter(endpoint_id=old_id).update(endpoint_id=new_id)
            Agent.objects.filter(preferred_contact_endpoint_id=old_id).update(preferred_contact_endpoint_id=new_id)
            AgentEmailAccount.objects.filter(endpoint_id=old_id).update(endpoint_id=new_id)
            AgentPeerLink.objects.filter(agent_a_endpoint_id=old_id).update(agent_a_endpoint_id=new_id)
            AgentPeerLink.objects.filter(agent_b_endpoint_id=old_id).update(agent_b_endpoint_id=new_id)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE api_persistentagentmessage_cc_endpoints
                    SET persistentagentcommsendpoint_id=%s
                    WHERE persistentagentcommsendpoint_id=%s
                    """,
                    [new_id, old_id],
                )

        for group in dup_groups:
            channel = group["channel"]
            addr_lower = group["addr_lower"]
            endpoints = list(
                Endpoint.objects.filter(channel=channel, address__iexact=addr_lower)
                .annotate(
                    has_owner=models.Case(
                        models.When(owner_agent_id__isnull=False, then=models.Value(1)),
                        default=models.Value(0),
                        output_field=models.IntegerField(),
                    )
                )
                .order_by("-is_primary", "-has_owner", "id")
            )
            if not endpoints:
                continue
            canonical = endpoints[0]
            for dup in endpoints[1:]:
                reassign(dup.id, canonical.id)
                dup.delete()

        Endpoint.objects.filter(channel="email").exclude(address=Lower("address")).update(address=Lower("address"))

    operations = [
        migrations.RunPython(dedupe_endpoints, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="persistentagentcommsendpoint",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="persistentagentcommsendpoint",
            constraint=models.UniqueConstraint(
                Lower("address"),
                "channel",
                name="pa_endpoint_ci_channel_address",
            ),
        ),
    ]
