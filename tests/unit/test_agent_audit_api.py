import json
import io
import zipfile
from datetime import timedelta
from uuid import uuid4
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
import zstandard as zstd
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase, tag
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentError,
    PersistentAgentJudgeSuggestion,
    PersistentAgentMessage,
    PersistentAgentPromptArchive,
    PersistentAgentStep,
    PersistentAgentSystemMessage,
    PersistentAgentToolCall,
)
from api.agent.core.agent_judge import NO_ACTION, REPORT_TOOL_NAME


def _judge_response(payload: dict):
    return MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=None,
                    tool_calls=[
                        {
                            "function": {
                                "name": REPORT_TOOL_NAME,
                                "arguments": json.dumps(payload),
                            }
                        }
                    ],
                )
            )
        ]
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

    def _read_export_archive(self, response):
        archive_bytes = b"".join(response.streaming_content)
        return zipfile.ZipFile(io.BytesIO(archive_bytes))

    def _read_export_payload(self, response):
        archive = self._read_export_archive(response)
        return json.loads(archive.read("audit-data.json").decode("utf-8"))

    def _create_agent_message(self, body):
        from_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=f"agent-{uuid4().hex}@example.com",
            is_primary=True,
        )
        to_ep = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=f"user-{uuid4().hex}@example.com",
        )
        return PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=from_ep,
            to_endpoint=to_ep,
            owner_agent=self.agent,
            body=body,
        )

    def test_process_events_endpoint_enqueues_task(self):
        with patch("console.api_views.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/process/")
        self.assertEqual(response.status_code, 202)
        mock_delay.assert_called_once_with(str(self.agent.id))
        payload = response.json()
        self.assertIn("queued", payload)
        self.assertIn("processing_active", payload)

    def test_manual_judge_endpoint_bypasses_cooldown(self):
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
        )
        response_payload = _judge_response(
            {
                "suggestion_type": NO_ACTION,
                "message": "No action needed.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ) as config_mock, patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response_payload,
        ) as run_mock:
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/judge/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ran"])
        config_mock.assert_called_once_with()
        run_mock.assert_called_once()
        self.assertEqual(
            PersistentAgentCompletion.objects.filter(
                agent=self.agent,
                completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            ).count(),
            2,
        )

    def test_manual_judge_endpoint_returns_handled_not_run_payload(self):
        with patch(
            "console.api_views.run_manual_agent_judge",
            return_value={"ran": False, "status": "llm_not_configured"},
        ):
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/judge/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ran": False, "status": "llm_not_configured"})

    def test_manual_judge_endpoint_returns_reviewable_suggestion(self):
        response_payload = _judge_response(
            {
                "suggestion_type": "strategy_shift",
                "message": "Try a different plan.",
                "agent_directive": "Draft a new approach before using another tool.",
            }
        )

        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            return_value=("test-provider", "test-model", {}),
        ), patch(
            "api.agent.core.agent_judge.run_completion",
            return_value=response_payload,
        ):
            response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/judge/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        suggestion_payload = payload["suggestion"]
        self.assertEqual(suggestion_payload["suggestionType"], "strategy_shift")
        self.assertEqual(suggestion_payload["status"], PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW)
        self.assertIn("/decision/", suggestion_payload["decisionApiUrl"])
        suggestion = PersistentAgentJudgeSuggestion.objects.get(id=suggestion_payload["suggestionId"])
        self.assertIsNone(suggestion.system_message)
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(agent=self.agent).exists())

    def test_manual_judge_suggestion_decision_approves_and_rejects(self):
        system_message = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Judge directive",
            is_active=False,
        )
        suggestion = PersistentAgentJudgeSuggestion.objects.create(
            agent=self.agent,
            suggestion_type=PersistentAgentJudgeSuggestion.SuggestionType.STRATEGY_SHIFT,
            title="Shift strategy",
            ui_message="Try another approach.",
            agent_directive="Use a different plan.",
            evidence_hash="manual-review-test",
            status=PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW,
            system_message=system_message,
        )

        decision_url = (
            f"/console/api/staff/agents/{self.agent.id}/audit/"
            f"judge-suggestions/{suggestion.id}/decision/"
        )
        approve_response = self.client.post(
            decision_url,
            data=json.dumps({"decision": "approve"}),
            content_type="application/json",
        )
        self.assertEqual(approve_response.status_code, 200)
        suggestion.refresh_from_db()
        system_message.refresh_from_db()
        self.assertEqual(suggestion.status, PersistentAgentJudgeSuggestion.Status.ACTIVE)
        self.assertTrue(system_message.is_active)

        reject_response = self.client.post(
            decision_url,
            data=json.dumps({"decision": "reject"}),
            content_type="application/json",
        )
        self.assertEqual(reject_response.status_code, 200)
        suggestion.refresh_from_db()
        system_message.refresh_from_db()
        self.assertEqual(suggestion.status, PersistentAgentJudgeSuggestion.Status.DISMISSED)
        self.assertFalse(system_message.is_active)

    def test_reject_pending_manual_judge_suggestion_does_not_leave_system_message(self):
        system_message = PersistentAgentSystemMessage.objects.create(
            agent=self.agent,
            body="Unapproved judge directive",
            is_active=False,
        )
        suggestion = PersistentAgentJudgeSuggestion.objects.create(
            agent=self.agent,
            suggestion_type=PersistentAgentJudgeSuggestion.SuggestionType.STRATEGY_SHIFT,
            title="Shift strategy",
            ui_message="Try another approach.",
            agent_directive="Use a different plan.",
            evidence_hash="manual-review-reject-test",
            status=PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW,
            system_message=system_message,
        )

        decision_url = (
            f"/console/api/staff/agents/{self.agent.id}/audit/"
            f"judge-suggestions/{suggestion.id}/decision/"
        )
        reject_response = self.client.post(
            decision_url,
            data=json.dumps({"decision": "reject"}),
            content_type="application/json",
        )

        self.assertEqual(reject_response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, PersistentAgentJudgeSuggestion.Status.DISMISSED)
        self.assertIsNone(suggestion.system_message)
        self.assertFalse(PersistentAgentSystemMessage.objects.filter(id=system_message.id).exists())

    def test_manual_judge_endpoint_requires_staff(self):
        self.client.force_login(self.nonstaff)
        response = self.client.post(f"/console/api/staff/agents/{self.agent.id}/audit/judge/")
        self.assertEqual(response.status_code, 403)

    def test_create_system_message(self):
        payload = {"body": "Priority directive"}
        response = self.client.post(
            f"/console/api/staff/agents/{self.agent.id}/system-messages/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data.get("kind"), "system_message")
        self.assertEqual(data.get("body"), "Priority directive")
        self.assertNotIn("is_active", data)

    def test_log_agent_error_helper_persists_and_logs(self):
        from api.services.agent_error_logging import log_agent_error

        mock_logger = MagicMock()

        error = log_agent_error(
            self.agent,
            category=PersistentAgentError.Category.OTHER,
            source="tests.agent_audit",
            message="Something failed",
            logger=mock_logger,
            context={"agent_id": str(self.agent.id), "detail": "safe"},
        )

        self.assertIsNotNone(error)
        mock_logger.log.assert_called_once()
        persisted = PersistentAgentError.objects.get(id=error.id)
        self.assertEqual(persisted.agent, self.agent)
        self.assertEqual(persisted.category, PersistentAgentError.Category.OTHER)
        self.assertEqual(persisted.context["detail"], "safe")

    def test_log_agent_error_helper_persists_truncated_traceback(self):
        from api.services.agent_error_logging import MAX_TRACEBACK_LENGTH, log_agent_error

        mock_logger = MagicMock()
        try:
            raise ValueError("bad value")
        except ValueError as exc:
            error = log_agent_error(
                self.agent,
                category=PersistentAgentError.Category.LLM_COMPLETION,
                source="tests.traceback",
                message="LLM failed",
                exc=exc,
                logger=mock_logger,
                context={"large": "x" * 5000},
            )

        self.assertIsNotNone(error)
        persisted = PersistentAgentError.objects.get(id=error.id)
        self.assertEqual(persisted.exception_class, "ValueError")
        self.assertIn("ValueError: bad value", persisted.traceback)
        self.assertLessEqual(len(persisted.traceback), MAX_TRACEBACK_LENGTH)
        self.assertLessEqual(len(persisted.context["large"]), 2000)

    def test_log_task_quota_exceeded_persists_traceback_without_server_exc_info(self):
        from django.core.exceptions import ValidationError

        from api.services.agent_error_logging import log_task_quota_exceeded

        mock_logger = MagicMock()
        try:
            raise ValidationError({"quota": "Task quota exceeded. No credits remain."})
        except ValidationError as exc:
            error = log_task_quota_exceeded(
                str(self.agent.id),
                exc,
                source="tests.quota_wrapper",
                logger=mock_logger,
                task_id="task-123",
            )

        self.assertIsNotNone(error)
        _, kwargs = mock_logger.log.call_args
        self.assertNotIn("exc_info", kwargs)
        persisted = PersistentAgentError.objects.get(id=error.id)
        self.assertEqual(persisted.category, PersistentAgentError.Category.TASK_QUOTA_EXCEEDED)
        self.assertIn("ValidationError", persisted.traceback)
        self.assertEqual(persisted.context["task_id"], "task-123")

    def test_audit_api_returns_error_events(self):
        PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.TASK_QUOTA_EXCEEDED,
            source="tests.quota",
            level="INFO",
            message="Task quota exceeded",
            exception_class="ValidationError",
            traceback="trace",
            context={"validation_messages": ["Task quota exceeded"]},
        )

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/?limit=10")
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        error_event = next(event for event in events if event["kind"] == "error")
        self.assertEqual(error_event["category"], PersistentAgentError.Category.TASK_QUOTA_EXCEEDED)
        self.assertEqual(error_event["source"], "tests.quota")
        self.assertEqual(error_event["context"]["validation_messages"], ["Task quota exceeded"])

    def test_audit_api_returns_completion_reasoning(self):
        PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
            thinking_content="Judge reasoning trace.",
            llm_model="ultra-max-test",
            llm_provider="test-provider",
        )

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/?limit=10")
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        completion_event = next(event for event in events if event["kind"] == "completion")
        self.assertEqual(completion_event["completion_type"], PersistentAgentCompletion.CompletionType.LLM_JUDGE)
        self.assertEqual(completion_event["thinking"], "Judge reasoning trace.")

    def test_audit_timeline_counts_error_events(self):
        PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.OTHER,
            source="tests.timeline",
            message="Timeline error",
        )

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/timeline/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(sum(bucket["count"] for bucket in payload["buckets"]), 1)

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
            llm_tool_names=["sqlite_batch", "send_email"],
            billed=True,
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
            channel=CommsChannel.EMAIL,
            address=f"agent-{uuid4().hex}@example.com",
            is_primary=True,
        )
        to_ep = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=f"user-{uuid4().hex}@example.com",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=from_ep,
            to_endpoint=to_ep,
            owner_agent=self.agent,
            body="Hello from the agent.",
            raw_payload={"body_html": "<p><strong>Hello</strong> from the agent.</p>"},
        )
        PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.LLM_COMPLETION,
            source="tests.export",
            level="ERROR",
            message="LLM failed",
            exception_class="RuntimeError",
            traceback="RuntimeError: failed",
            context={"provider_candidates": [{"provider": "openrouter", "model": "test"}]},
        )

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment; filename=", response["Content-Disposition"])

        archive = self._read_export_archive(response)
        names = set(archive.namelist())
        self.assertIn("index.html", names)
        self.assertIn("audit-data.json", names)
        self.assertIn("audit-data.js", names)
        self.assertIn("viewer.js", names)

        export_payload = json.loads(archive.read("audit-data.json").decode("utf-8"))
        self.assertIn("exported_at", export_payload)
        self.assertIn("completions", export_payload)
        self.assertIn("messages", export_payload)
        self.assertEqual(export_payload["range"]["key"], "all")
        self.assertEqual(export_payload["range"]["label"], "Full audit")
        self.assertIsNone(export_payload["range"]["start"])
        self.assertIsNotNone(export_payload["range"]["end"])
        self.assertEqual(export_payload["counts"]["completions"], 1)
        self.assertEqual(export_payload["counts"]["messages"], 1)
        self.assertEqual(export_payload["counts"]["errors"], 1)
        self.assertEqual(export_payload["errors"][0]["kind"], "error")
        self.assertEqual(export_payload["errors"][0]["category"], PersistentAgentError.Category.LLM_COMPLETION)

        exported_completion = export_payload["completions"][0]
        self.assertIsNotNone(exported_completion.get("timestamp"))
        self.assertEqual(exported_completion.get("thinking"), "Reasoning trace.")
        self.assertEqual(exported_completion.get("llm_tool_names"), ["sqlite_batch", "send_email"])
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
        self.assertEqual(exported_message.get("body_html"), "<p><strong>Hello</strong> from the agent.</p>")

    def test_audit_export_24h_range_filters_rows_and_reports_metadata(self):
        old_at = timezone.now() - timedelta(days=2)
        recent_completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            llm_model="openrouter/recent-model",
            llm_provider="openrouter",
            billed=True,
        )
        old_completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            llm_model="openrouter/old-model",
            llm_provider="openrouter",
            billed=True,
        )
        PersistentAgentCompletion.objects.filter(id=old_completion.id).update(created_at=old_at)

        recent_message = self._create_agent_message("recent message")
        old_message = self._create_agent_message("old message")
        PersistentAgentMessage.objects.filter(id=old_message.id).update(timestamp=old_at)

        recent_error = PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.OTHER,
            source="tests.export.recent",
            message="Recent error",
        )
        old_error = PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.OTHER,
            source="tests.export.old",
            message="Old error",
        )
        PersistentAgentError.objects.filter(id=old_error.id).update(created_at=old_at)

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/?range=24h")

        self.assertEqual(response.status_code, 200)
        export_payload = self._read_export_payload(response)
        self.assertEqual(export_payload["range"]["key"], "24h")
        self.assertEqual(export_payload["range"]["label"], "Last 24 hours")
        self.assertIsNotNone(export_payload["range"]["start"])
        self.assertIsNotNone(export_payload["range"]["end"])
        self.assertEqual(export_payload["counts"], {"completions": 1, "messages": 1, "errors": 1})
        self.assertEqual([item["id"] for item in export_payload["completions"]], [str(recent_completion.id)])
        self.assertEqual([item["id"] for item in export_payload["messages"]], [str(recent_message.id)])
        self.assertEqual([item["id"] for item in export_payload["errors"]], [str(recent_error.id)])

    def test_audit_export_all_range_includes_older_rows(self):
        old_at = timezone.now() - timedelta(days=2)
        old_completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            llm_model="openrouter/old-model",
            llm_provider="openrouter",
            billed=True,
        )
        PersistentAgentCompletion.objects.filter(id=old_completion.id).update(created_at=old_at)
        old_message = self._create_agent_message("old message")
        PersistentAgentMessage.objects.filter(id=old_message.id).update(timestamp=old_at)
        old_error = PersistentAgentError.objects.create(
            agent=self.agent,
            category=PersistentAgentError.Category.OTHER,
            source="tests.export.old",
            message="Old error",
        )
        PersistentAgentError.objects.filter(id=old_error.id).update(created_at=old_at)

        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/?range=all")

        self.assertEqual(response.status_code, 200)
        export_payload = self._read_export_payload(response)
        self.assertEqual(export_payload["range"]["key"], "all")
        self.assertEqual(export_payload["counts"], {"completions": 1, "messages": 1, "errors": 1})
        self.assertEqual([item["id"] for item in export_payload["completions"]], [str(old_completion.id)])
        self.assertEqual([item["id"] for item in export_payload["messages"]], [str(old_message.id)])
        self.assertEqual([item["id"] for item in export_payload["errors"]], [str(old_error.id)])

    def test_audit_export_rejects_invalid_range(self):
        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/?range=yesterday")

        self.assertEqual(response.status_code, 400)

    def test_audit_export_handles_missing_s3_prompt_archive_payload(self):
        completion = PersistentAgentCompletion.objects.create(
            agent=self.agent,
            completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            llm_model="openrouter/test-model",
            llm_provider="openrouter",
            billed=True,
        )
        prompt_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            completion=completion,
            description="Prompt attached step",
        )
        storage_key = f"test/audit_export/missing-{uuid4().hex}.json.zst"
        PersistentAgentPromptArchive.objects.create(
            agent=self.agent,
            rendered_at=prompt_step.created_at,
            storage_key=storage_key,
            raw_bytes=100,
            compressed_bytes=50,
            tokens_before=10,
            tokens_after=8,
            tokens_saved=2,
            step=prompt_step,
        )
        missing_object_error = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "object does not exist"}},
            "GetObject",
        )

        with patch("console.agent_audit.export.default_storage.open", side_effect=missing_object_error):
            response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/")

        self.assertEqual(response.status_code, 200)
        archive_bytes = b"".join(response.streaming_content)
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        export_payload = json.loads(archive.read("audit-data.json").decode("utf-8"))
        exported_completion = export_payload["completions"][0]
        prompt_payload = exported_completion["prompt_archive"]["payload"]
        self.assertEqual(prompt_payload, {"error": "missing_payload"})

    def test_audit_export_requires_staff(self):
        self.client.force_login(self.nonstaff)
        response = self.client.get(f"/console/api/staff/agents/{self.agent.id}/audit/export/")
        self.assertEqual(response.status_code, 403)
