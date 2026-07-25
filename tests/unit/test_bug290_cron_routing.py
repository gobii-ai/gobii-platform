"""#290 verification: does a cron step firing mid-job nullify the reply routing context?

Investigation artifact. The ticket claims a cron trigger arriving after the inbound message
nulls the reply target for the in-flight run.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.comms.routing import (
    bind_inbound_routing_scope,
    capture_inbound_routing_scope,
    get_current_inbound_message,
    reset_inbound_routing_scope,
)
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentCronTrigger,
    PersistentAgentMessage,
    PersistentAgentStep,
)


@tag("batch_api_agents")
class Bug290CronRoutingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="cron-routing@example.test",
            email="cron-routing@example.test",
        )
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Scheduled Agent",
            charter="Reply to the user.",
            schedule="0 13 * * *",
            browser_use_agent=BrowserUseAgent.objects.create(user=user, name="browser"),
        )
        conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.WEB,
            address="web://user/1",
            display_name="Web chat",
        )
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address="web://user/1",
        )
        self.inbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=endpoint,
            conversation=conversation,
            is_outbound=False,
            body="does that all check out?",
        )

    def _fire_cron(self, when):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Cron trigger")
        PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=when)
        PersistentAgentCronTrigger.objects.create(step=step, cron_expression="0 13 * * *")
        return step

    def test_cron_firing_mid_job_keeps_the_reply_target(self):
        """The scenario #290 describes: job starts, then cron fires while it is still running."""
        job_started_at = timezone.now()
        scope = capture_inbound_routing_scope(self.agent, background_before=job_started_at)
        token = bind_inbound_routing_scope(scope)
        try:
            self._fire_cron(job_started_at + timedelta(seconds=30))
            current = get_current_inbound_message(self.agent)
        finally:
            reset_inbound_routing_scope(token)

        self.assertIsNotNone(current, "cron firing mid-job nulled the reply target")
        self.assertEqual(current.id, self.inbound.id)

    def test_cron_before_the_job_starts_is_still_treated_as_background(self):
        """Intended behaviour: a cron-initiated run is a background wake, not a reply."""
        cron_at = timezone.now()
        self._fire_cron(cron_at)
        scope = capture_inbound_routing_scope(self.agent, background_before=cron_at + timedelta(seconds=30))
        token = bind_inbound_routing_scope(scope)
        try:
            current = get_current_inbound_message(self.agent)
        finally:
            reset_inbound_routing_scope(token)

        self.assertIsNone(current)

    def test_unscoped_lookup_is_the_only_path_that_nulls(self):
        """Without a bound scope the unbounded check nulls the target — the ticket's mechanism."""
        self._fire_cron(timezone.now())
        self.assertIsNone(get_current_inbound_message(self.agent))
