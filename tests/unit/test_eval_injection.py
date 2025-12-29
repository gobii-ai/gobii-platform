
from django.core.files.base import ContentFile
from unittest.mock import patch, MagicMock
from django.test import TestCase, tag
from django.contrib.auth import get_user_model
from api.models import PersistentAgent, CommsChannel, PersistentAgentMessage, BrowserUseAgent
from api.agent.comms.message_service import inject_internal_web_message

User = get_user_model()

@tag("batch_event_processing")
class EvalInjectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eval-test-user",
            email="eval-test-user@example.com",
            password="password123"
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            name="Eval Browser Agent",
            user=self.user
        )
        self.agent = PersistentAgent.objects.create(
            name="Eval Test Agent",
            user=self.user,
            charter="You are a helpful assistant.",
            browser_use_agent=self.browser_agent
        )

    def test_inject_internal_web_message_no_trigger(self):
        """Test injecting a message without triggering processing."""
        body = "Hello from test"
        sender_id = -123
        
        # We patch process_agent_events_task to ensure it's NOT called
        with patch("api.agent.tasks.process_agent_events_task.delay") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                msg, conv = inject_internal_web_message(
                    agent_id=self.agent.id,
                    body=body,
                    sender_user_id=sender_id,
                    trigger_processing=False
                )
            
            mock_task.assert_not_called()
            
        # Verification
        self.assertEqual(msg.body, body)
        self.assertEqual(msg.owner_agent_id, self.agent.id)
        self.assertEqual(msg.conversation, conv)
        self.assertEqual(msg.raw_payload.get("sender_user_id"), sender_id)
        self.assertFalse(msg.is_outbound)
        
        # Check endpoints
        self.assertEqual(msg.from_endpoint.channel, CommsChannel.WEB.value)
        self.assertEqual(msg.to_endpoint.channel, CommsChannel.WEB.value)
        self.assertEqual(msg.to_endpoint.owner_agent_id, self.agent.id)

    def test_inject_internal_web_message_with_trigger(self):
        """Test injecting a message triggers processing by default."""
        body = "Trigger me"
        
        with patch("api.agent.tasks.process_agent_events_task.delay") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                msg, conv = inject_internal_web_message(
                    agent_id=self.agent.id,
                    body=body,
                    trigger_processing=True
                )
            
            mock_task.assert_called_once_with(str(self.agent.id), eval_run_id=None)
            
        self.assertEqual(msg.body, body)

    def test_inject_message_creates_endpoints_correctly(self):
        """Test that endpoints are created and linked correctly."""
        body = "Endpoint test"
        sender_id = -555
        
        msg, conv = inject_internal_web_message(
            agent_id=self.agent.id,
            body=body,
            sender_user_id=sender_id,
            trigger_processing=False
        )
        
        # Verify sender endpoint
        self.assertTrue(str(sender_id) in msg.from_endpoint.address)
        self.assertEqual(msg.from_endpoint.channel, CommsChannel.WEB.value)
        
        # Verify agent endpoint
        self.assertEqual(msg.to_endpoint.owner_agent_id, self.agent.id)
        self.assertEqual(msg.to_endpoint.channel, CommsChannel.WEB.value)
        
        # Verify conversation participants
        participants = conv.participants.all()
        self.assertEqual(participants.count(), 2)
        roles = set(p.role for p in participants)
        self.assertIn("human_user", roles)
        self.assertIn("agent", roles)

    def test_inject_message_with_attachments(self):
        """Test that attachments are handled (basic smoke test)."""
        
        # Use ContentFile which mimics a Django file object nicely
        dummy_file = ContentFile(b"test content", name="test.txt")

        with patch("api.agent.comms.message_service.import_message_attachments_to_filespace") as mock_import:
            with self.captureOnCommitCallbacks(execute=True):
                msg, conv = inject_internal_web_message(
                    agent_id=self.agent.id,
                    body="Attachment test",
                    attachments=[dummy_file],
                    trigger_processing=False
                )
            mock_import.assert_called_once_with(str(msg.id))
