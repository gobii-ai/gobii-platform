import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.multimodal_context import (
    attach_read_file_images_to_messages,
    collect_fresh_read_file_image_attachments,
    filter_vision_capable_failover_configs,
    prepare_multimodal_read_file_request,
)
from api.agent.core.prompt_context import build_prompt_context
from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("batch_event_llm")
class MultimodalReadFileContextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="multimodal-read-file@example.com",
            email="multimodal-read-file@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Multimodal Browser")
        cls.agent = PersistentAgent.objects.create(
            user=user,
            name="Multimodal Agent",
            charter="Inspect files.",
            browser_use_agent=browser_agent,
        )

    def _write_file(self, *, path: str, content: bytes, mime_type: str) -> None:
        result = write_bytes_to_dir(
            agent=self.agent,
            content_bytes=content,
            path=path,
            mime_type=mime_type,
            overwrite=True,
        )
        self.assertEqual(result.get("status"), "ok")

    def _create_read_file_step(
        self,
        *,
        path: str,
        result_status: str = "ok",
        tool_status: str = "complete",
        completion: PersistentAgentCompletion | None = None,
    ) -> PersistentAgentStep:
        if completion is None:
            completion = PersistentAgentCompletion.objects.create(agent=self.agent)
        step = PersistentAgentStep.objects.create(
            agent=self.agent,
            completion=completion,
            description=f"Tool read_file called for {path}",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="read_file",
            tool_params={"path": path},
            result=json.dumps({"status": result_status, "format": "markdown", "markdown": "converted"}),
            status=tool_status,
        )
        return step

    def test_collects_only_fresh_successful_supported_images(self):
        self._write_file(path="/images/photo.png", content=b"\x89PNG\r\nimage", mime_type="image/png")
        self._write_file(path="/images/note.txt", content=b"hello", mime_type="text/plain")
        completion = PersistentAgentCompletion.objects.create(agent=self.agent)
        image_step = self._create_read_file_step(path="/images/photo.png", completion=completion)
        text_step = self._create_read_file_step(path="/images/note.txt", completion=completion)
        failed_step = self._create_read_file_step(
            path="/images/photo.png",
            result_status="error",
            completion=completion,
        )

        attachments = collect_fresh_read_file_image_attachments(
            self.agent,
            {str(image_step.id), str(text_step.id), str(failed_step.id)},
        )

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].path, "/images/photo.png")
        self.assertEqual(attachments[0].mime_type, "image/png")
        self.assertTrue(attachments[0].data_url.startswith("data:image/png;base64,"))

    def test_collects_multiple_fresh_images_with_cap(self):
        steps = []
        completion = PersistentAgentCompletion.objects.create(agent=self.agent)
        for idx in range(4):
            path = f"/images/photo-{idx}.png"
            self._write_file(path=path, content=f"png-{idx}".encode("utf-8"), mime_type="image/png")
            steps.append(self._create_read_file_step(path=path, completion=completion))

        attachments = collect_fresh_read_file_image_attachments(
            self.agent,
            {str(step.id) for step in steps},
            max_images=2,
        )

        self.assertEqual(len(attachments), 2)

    @patch("api.agent.core.multimodal_context.get_max_file_size", return_value=4)
    def test_collect_skips_oversized_image(self, _mock_max_size):
        self._write_file(path="/images/large.png", content=b"too-large", mime_type="image/png")
        step = self._create_read_file_step(path="/images/large.png")

        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})

        self.assertEqual(attachments, [])

    def test_collect_skips_read_file_image_after_newer_orchestrator_completion(self):
        self._write_file(path="/images/old.png", content=b"png", mime_type="image/png")
        step = self._create_read_file_step(path="/images/old.png")
        PersistentAgentCompletion.objects.create(agent=self.agent)

        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})

        self.assertEqual(attachments, [])

    def test_filter_vision_capable_failover_configs_preserves_order(self):
        configs = [
            ("text-primary", "model-a", {"supports_vision": False}),
            ("vision-primary", "model-b", {"supports_vision": True}),
            ("vision-fallback", "model-c", {"supports_vision": True}),
        ]

        filtered = filter_vision_capable_failover_configs(configs)

        self.assertEqual(
            filtered,
            [
                ("vision-primary", "model-b", {"supports_vision": True}),
                ("vision-fallback", "model-c", {"supports_vision": True}),
            ],
        )

    def test_prepare_multimodal_request_attaches_images_only_with_vision_config(self):
        self._write_file(path="/images/photo.webp", content=b"webp", mime_type="image/webp")
        step = self._create_read_file_step(path="/images/photo.webp")
        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Inspect it."}]
        configs = [
            ("text-primary", "model-a", {"supports_vision": False}),
            ("vision-primary", "model-b", {"supports_vision": True}),
        ]

        updated_messages, updated_configs, attached = prepare_multimodal_read_file_request(
            messages,
            configs,
            attachments,
        )

        self.assertTrue(attached)
        self.assertEqual(updated_configs, [("vision-primary", "model-b", {"supports_vision": True})])
        user_content = updated_messages[-1]["content"]
        self.assertEqual(user_content[0], {"type": "text", "text": "Inspect it."})
        self.assertEqual(user_content[1], {"type": "text", "text": "Image from read_file: /images/photo.webp"})
        self.assertEqual(user_content[2]["type"], "image_url")
        self.assertTrue(user_content[2]["image_url"]["url"].startswith("data:image/webp;base64,"))

    def test_prepare_multimodal_request_keeps_text_only_when_no_vision_config(self):
        self._write_file(path="/images/photo.gif", content=b"gif", mime_type="image/gif")
        step = self._create_read_file_step(path="/images/photo.gif")
        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})
        messages = [{"role": "user", "content": "Inspect it."}]
        configs = [("text-primary", "model-a", {"supports_vision": False})]

        updated_messages, updated_configs, attached = prepare_multimodal_read_file_request(
            messages,
            configs,
            attachments,
        )

        self.assertFalse(attached)
        self.assertEqual(updated_messages, messages)
        self.assertEqual(updated_configs, configs)

    @patch("api.agent.core.event_processing.get_llm_config_with_failover")
    def test_orchestrator_can_fetch_uncapped_vision_configs_for_image_context(self, mock_get_configs):
        from api.agent.core import event_processing

        self._write_file(path="/images/photo.png", content=b"png", mime_type="image/png")
        step = self._create_read_file_step(path="/images/photo.png")
        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})
        capped_configs = [("standard-text", "model-a", {"supports_vision": False})]
        uncapped_configs = [
            ("ultra-vision", "model-b", {"supports_vision": True}),
            ("standard-text", "model-a", {"supports_vision": False}),
        ]
        mock_get_configs.return_value = uncapped_configs

        candidate_configs = capped_configs
        if not any(bool((params or {}).get("supports_vision")) for _, _, params in capped_configs):
            candidate_configs = event_processing.get_llm_config_with_failover(
                agent_id=str(self.agent.id),
                token_count=123,
                agent=self.agent,
                is_first_loop=False,
                routing_profile=None,
                prefer_low_latency=False,
                ignore_agent_tier_cap=True,
            )
        updated_messages, updated_configs, attached = prepare_multimodal_read_file_request(
            [{"role": "user", "content": "Inspect it."}],
            candidate_configs,
            attachments,
        )

        self.assertTrue(attached)
        self.assertEqual(updated_configs, [("ultra-vision", "model-b", {"supports_vision": True})])
        self.assertEqual(updated_messages[0]["content"][2]["type"], "image_url")
        mock_get_configs.assert_called_once_with(
            agent_id=str(self.agent.id),
            token_count=123,
            agent=self.agent,
            is_first_loop=False,
            routing_profile=None,
            prefer_low_latency=False,
            ignore_agent_tier_cap=True,
        )

    def test_attach_read_file_images_to_messages_preserves_existing_list_content(self):
        self._write_file(path="/images/photo.jpg", content=b"jpeg", mime_type="image/jpeg")
        step = self._create_read_file_step(path="/images/photo.jpg")
        attachments = collect_fresh_read_file_image_attachments(self.agent, {str(step.id)})
        messages = [{"role": "user", "content": [{"type": "text", "text": "Existing"}]}]

        updated_messages = attach_read_file_images_to_messages(messages, attachments)

        self.assertEqual(updated_messages[0]["content"][0], {"type": "text", "text": "Existing"})
        self.assertEqual(updated_messages[0]["content"][1]["text"], "Image from read_file: /images/photo.jpg")
        self.assertEqual(updated_messages[0]["content"][2]["type"], "image_url")

    def test_prompt_metadata_exposes_fresh_tool_call_step_ids(self):
        step = self._create_read_file_step(path="/images/photo.png")

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {"allow_implied_send": True})],
        ):
            _context, _tokens, _archive_id, metadata = build_prompt_context(
                self.agent,
                include_metadata=True,
            )

        self.assertIn(str(step.id), metadata["fresh_tool_call_step_ids"])
