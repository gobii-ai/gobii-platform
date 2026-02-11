import json
import io
import zipfile
from uuid import uuid4
from unittest.mock import patch

import zstandard as zstd
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase, tag

from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentPromptArchive,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("batch_console_api")
class StaffAgentAuditAPITests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="staff-admin",
            email="staff@example.com",
            password="pass123",
            is_staff=True,
        )
        self.nonstaff = user_model.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="pass123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.nonstaff, name="Browser Agent")
        self.agent = PersistentAgent.objects.create(
            user=self.nonstaff,
            name="Audit Target",
            charter="Do things",
            browser_use_agent=self.browser_agent,
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def test_process_events_endpoint_enqueues_task(self):
        with patch("console.api_views.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/process/")
        self.assertEqual(response.status_code, 202)
        mock_delay.assert_called_once_with(str(self.agent.id))
        payload = response.json()
        self.assertIn("queued", payload)
        self.assertIn("processing_active", payload)

    def test_create_system_message(self):
        payload = {"body": "Priority directive", "is_active": True}
        response = self.client.post(
            f"/console/api/staff/agents/{self.agent.id}/system-messages/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data.get("kind"), "system_message")
        self.assertEqual(data.get("body"), "Priority directive")

    def test_system_message_requires_staff(self):
        self.client.force_login(self.nonstaff)
        response = self.client.post(
            f"/console/api/staff/agents/{self.agent.id}/system-messages/",
            data=json.dumps({"body": "nope"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_audit_export_download_contains_html_and_json(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            llm_model="openrouter/test-model",
            llm_provider="openrouter",
            thinking_content="Reasoning trace.",
            prompt_tokens=111,
            completion_tokens=22,
            total_tokens=133,
            cached_tokens=11,
            response_id="resp-123",
        )
        prompt_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            completion=completion,
            description="Prompt attached step",
        )
        tool_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            completion=completion,
            description="Tool call: weather",
        )
        PersistentAgentToolCall.objects.create(
            step=tool_step,
            tool_name="weather",
            tool_params={"location": "NYC"},
            result='{"temp_f": 70}',
            execution_duration_ms=250,
        )
        archive_payload = {
            "agent_id": str(self.agent.id),
            "rendered_at": "2026-02-11T00:00:00Z",
            "system_prompt": "system prompt text",
            "user_prompt": "user prompt text",
            "token_budget": 12345,
            "tokens_before": 10,
            "tokens_after": 8,
            "tokens_saved": 2,
        }
        archive_bytes = json.dumps(archive_payload).encode("utf-8")
        storage_key = f"test/audit_export/{uuid4().hex}.json.zst"
        compressed = zstd.ZstdCompressor(level=3).compress(archive_bytes)
        default_storage.save(storage_key, ContentFile(compressed))
        PersistentAgentPromptArchive.objects.create(
            agent=self.agent,
            rendered_at=prompt_step.created_at,
            storage_key=storage_key,
            raw_bytes=len(archive_bytes),
            compressed_bytes=len(compressed),
            tokens_before=10,
            tokens_after=8,
            tokens_saved=2,
            step=prompt_step,
        )
        from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"agent-{uuid4().hex}@example.com",
            is_primary=True,
        )
        to_ep = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=f"user-{uuid4().hex}@example.com",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=from_ep,
            to_endpoint=to_ep,
            owner_agent=self.agent,
            body="Hello from the agent.",
        )

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment; filename=", response["Content-Disposition"])

        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        self.assertIn("index.html", names)
        self.assertIn("audit-data.json", names)

        export_payload = json.loads(archive.read("audit-data.json").decode("utf-8"))
        self.assertIn("exported_at", export_payload)
        self.assertIn("completions", export_payload)
        self.assertIn("messages", export_payload)
        self.assertEqual(export_payload["counts"]["completions"], 1)
        self.assertEqual(export_payload["counts"]["messages"], 1)

        exported_completion = export_payload["completions"][0]
        self.assertIsNotNone(exported_completion.get("timestamp"))
        self.assertEqual(exported_completion.get("thinking"), "Reasoning trace.")
        prompt_archive = exported_completion.get("prompt_archive") or {}
        prompt_payload = prompt_archive.get("payload") or {}
        self.assertEqual(prompt_payload.get("system_prompt"), "system prompt text")
        self.assertEqual(prompt_payload.get("user_prompt"), "user prompt text")

        tool_calls = exported_completion.get("tool_calls") or []
        self.assertEqual(len(tool_calls), 1)
        self.assertIsNotNone(tool_calls[0].get("timestamp"))
        self.assertEqual(tool_calls[0].get("parameters", {}).get("location"), "NYC")

        exported_message = export_payload["messages"][0]
        self.assertIsNotNone(exported_message.get("timestamp"))
        self.assertEqual(exported_message.get("body_text"), "Hello from the agent.")

    def test_audit_export_requires_staff(self):
        self.client.force_login(self.nonstaff)
        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/")
        self.assertEqual(response.status_code, 403)
