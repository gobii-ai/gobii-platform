from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from django.test import TestCase, tag

from api.models import PersistentAgentCompletion
from api.agent.tools.create_video import (
    GeneratedVideoResult,
    VideoGenerationResponseError,
    _generate_video,
    _wait_for_video_completion,
    get_create_video_tool,
    execute_create_video,
    is_video_generation_available_for_agent,
)
from api.agent.core.video_generation_config import VideoGenerationLLMConfig


def _make_config(**overrides):
    defaults = {
        "model": "sora-2",
        "params": {"api_key": "sk-test"},
        "endpoint_key": "test-ep",
        "supports_image_to_video": False,
    }
    defaults.update(overrides)
    return VideoGenerationLLMConfig(**defaults)


def _make_video_obj(status="completed", video_id="vid-123", error=None):
    obj = MagicMock()
    obj.id = video_id
    obj.status = status
    obj.error = error
    obj.usage = None
    return obj


@tag("batch_video_generation")
class GetCreateVideoToolTests(TestCase):
    def test_tool_schema_has_required_fields(self):
        tool = get_create_video_tool()
        self.assertEqual(tool["type"], "function")
        func = tool["function"]
        self.assertEqual(func["name"], "create_video")
        params = func["parameters"]
        self.assertIn("prompt", params["properties"])
        self.assertIn("file_path", params["properties"])
        self.assertIn("duration", params["properties"])
        self.assertIn("size", params["properties"])
        self.assertIn("source_image", params["properties"])
        self.assertEqual(params["required"], ["prompt", "file_path"])


@tag("batch_video_generation")
class IsVideoGenerationAvailableTests(TestCase):
    def test_returns_false_for_none_agent(self):
        self.assertFalse(is_video_generation_available_for_agent(None))

    @patch("api.agent.tools.create_video.is_create_video_generation_configured", return_value=True)
    def test_returns_true_when_configured(self, mock_configured):
        agent = MagicMock()
        self.assertTrue(is_video_generation_available_for_agent(agent))

    @patch("api.agent.tools.create_video.is_create_video_generation_configured", return_value=False)
    def test_returns_false_when_not_configured(self, mock_configured):
        agent = MagicMock()
        self.assertFalse(is_video_generation_available_for_agent(agent))


@tag("batch_video_generation")
class WaitForVideoCompletionTests(TestCase):
    @patch("api.agent.tools.create_video.litellm")
    @patch("api.agent.tools.create_video.time.sleep")
    def test_returns_immediately_if_completed(self, mock_sleep, mock_litellm):
        video_obj = _make_video_obj(status="completed")
        result = _wait_for_video_completion(video_obj, params={"api_key": "sk-test"})
        self.assertEqual(result.status, "completed")
        mock_sleep.assert_not_called()

    @patch("api.agent.tools.create_video.litellm")
    @patch("api.agent.tools.create_video.time.sleep")
    def test_polls_until_completed(self, mock_sleep, mock_litellm):
        pending_obj = _make_video_obj(status="pending")
        completed_obj = _make_video_obj(status="completed")
        mock_litellm.video_status.return_value = completed_obj

        result = _wait_for_video_completion(pending_obj, params={"api_key": "sk-test"})
        self.assertEqual(result.status, "completed")
        mock_sleep.assert_called_once()

    @patch("api.agent.tools.create_video.litellm")
    @patch("api.agent.tools.create_video.time.sleep")
    def test_raises_on_failure(self, mock_sleep, mock_litellm):
        pending_obj = _make_video_obj(status="pending")
        failed_obj = _make_video_obj(status="failed", error={"message": "content moderation"})
        mock_litellm.video_status.return_value = failed_obj

        with self.assertRaises(VideoGenerationResponseError) as ctx:
            _wait_for_video_completion(pending_obj, params={"api_key": "sk-test"})
        self.assertIn("content moderation", str(ctx.exception))

    @patch("api.agent.tools.create_video.MAX_POLL_SECONDS", 3)
    @patch("api.agent.tools.create_video.POLL_INTERVAL_SECONDS", 2)
    @patch("api.agent.tools.create_video.litellm")
    @patch("api.agent.tools.create_video.time.sleep")
    def test_raises_on_timeout(self, mock_sleep, mock_litellm):
        pending_obj = _make_video_obj(status="pending")
        mock_litellm.video_status.return_value = pending_obj

        with self.assertRaises(VideoGenerationResponseError) as ctx:
            _wait_for_video_completion(pending_obj, params={"api_key": "sk-test"})
        self.assertIn("timed out", str(ctx.exception))


@tag("batch_video_generation")
class GenerateVideoTests(TestCase):
    @patch("api.agent.tools.create_video.litellm")
    def test_generates_video_successfully(self, mock_litellm):
        video_obj = _make_video_obj(status="completed")
        mock_litellm.video_generation.return_value = video_obj
        mock_litellm.video_content.return_value = b"\x00\x00video-bytes"

        config = _make_config()
        result = _generate_video(config, prompt="A sunset over the ocean")

        self.assertIsInstance(result, GeneratedVideoResult)
        self.assertEqual(result.video_bytes, b"\x00\x00video-bytes")
        self.assertEqual(result.mime_type, "video/mp4")
        mock_litellm.video_generation.assert_called_once()

    @patch("api.agent.tools.create_video.litellm")
    def test_passes_duration_and_size(self, mock_litellm):
        video_obj = _make_video_obj(status="completed")
        mock_litellm.video_generation.return_value = video_obj
        mock_litellm.video_content.return_value = b"video"

        config = _make_config()
        _generate_video(config, prompt="test", duration="10", size="1920x1080")

        call_kwargs = mock_litellm.video_generation.call_args[1]
        self.assertEqual(call_kwargs["seconds"], "10")
        self.assertEqual(call_kwargs["size"], "1920x1080")

    @patch("api.agent.tools.create_video.litellm")
    def test_passes_source_image_as_input_reference(self, mock_litellm):
        video_obj = _make_video_obj(status="completed")
        mock_litellm.video_generation.return_value = video_obj
        mock_litellm.video_content.return_value = b"video"

        config = _make_config(supports_image_to_video=True)
        image_bytes = b"\x89PNG\r\n"
        _generate_video(config, prompt="animate this", source_image_bytes=image_bytes)

        call_kwargs = mock_litellm.video_generation.call_args[1]
        self.assertEqual(call_kwargs["input_reference"], image_bytes)

    @patch("api.agent.tools.create_video.litellm")
    def test_raises_on_empty_content(self, mock_litellm):
        video_obj = _make_video_obj(status="completed")
        mock_litellm.video_generation.return_value = video_obj
        mock_litellm.video_content.return_value = b""

        config = _make_config()
        with self.assertRaises(VideoGenerationResponseError):
            _generate_video(config, prompt="test")


@tag("batch_video_generation")
class ExecuteCreateVideoTests(TestCase):
    def test_missing_prompt_returns_error(self):
        agent = MagicMock()
        result = execute_create_video(agent, {"file_path": "/exports/test.mp4"})
        self.assertEqual(result["status"], "error")
        self.assertIn("prompt", result["message"])

    @patch("api.agent.tools.create_video.get_create_video_generation_llm_configs", return_value=[])
    @patch("api.agent.tools.create_video.resolve_export_target")
    def test_no_configs_returns_error(self, mock_resolve, mock_configs):
        mock_resolve.return_value = ("/exports/test.mp4", False, None)
        agent = MagicMock()
        result = execute_create_video(agent, {"prompt": "a sunset", "file_path": "/exports/test.mp4"})
        self.assertEqual(result["status"], "error")
        self.assertIn("No video generation model", result["message"])

    @patch("api.agent.tools.create_video.set_agent_variable")
    @patch("api.agent.tools.create_video.build_signed_filespace_download_url", return_value="https://signed/url")
    @patch("api.agent.tools.create_video.write_bytes_to_dir")
    @patch("api.agent.tools.create_video._log_video_generation_completion")
    @patch("api.agent.tools.create_video._generate_video")
    @patch("api.agent.tools.create_video.get_create_video_generation_llm_configs")
    @patch("api.agent.tools.create_video.resolve_export_target")
    def test_successful_generation(
        self,
        mock_resolve,
        mock_configs,
        mock_generate,
        mock_log,
        mock_write,
        mock_signed_url,
        mock_set_var,
    ):
        mock_resolve.return_value = ("/exports/test.mp4", False, None)
        config = _make_config()
        mock_configs.return_value = [config]
        mock_generate.return_value = GeneratedVideoResult(
            video_bytes=b"video-data",
            mime_type="video/mp4",
            response=_make_video_obj(),
        )
        mock_write.return_value = {"status": "ok", "path": "/exports/test.mp4", "node_id": "node-123"}

        agent = MagicMock()
        agent.id = "agent-123"
        result = execute_create_video(agent, {"prompt": "a sunset", "file_path": "/exports/test.mp4"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["file"], "$[/exports/test.mp4]")
        self.assertEqual(result["model"], "sora-2")
        mock_write.assert_called_once()

    @patch("api.agent.tools.create_video._log_video_generation_completion")
    @patch("api.agent.tools.create_video._generate_video")
    @patch("api.agent.tools.create_video._resolve_source_image_bytes")
    @patch("api.agent.tools.create_video.get_create_video_generation_llm_configs")
    @patch("api.agent.tools.create_video.resolve_export_target")
    def test_skips_endpoint_without_image_to_video_support(
        self,
        mock_resolve,
        mock_configs,
        mock_resolve_img,
        mock_generate,
        mock_log,
    ):
        mock_resolve.return_value = ("/exports/test.mp4", False, None)
        mock_resolve_img.return_value = (b"\x89PNG", None)
        config_no_img = _make_config(endpoint_key="no-img", supports_image_to_video=False)
        mock_configs.return_value = [config_no_img]

        agent = MagicMock()
        result = execute_create_video(agent, {
            "prompt": "animate",
            "file_path": "/exports/test.mp4",
            "source_image": "/inbox/photo.png",
        })

        self.assertEqual(result["status"], "error")
        self.assertIn("does not support image-to-video", result["message"])
        mock_generate.assert_not_called()

    @patch("api.agent.tools.create_video.set_agent_variable")
    @patch("api.agent.tools.create_video.build_signed_filespace_download_url", return_value="https://signed/url")
    @patch("api.agent.tools.create_video.write_bytes_to_dir")
    @patch("api.agent.tools.create_video.log_agent_completion")
    @patch("api.agent.tools.create_video.litellm")
    @patch("api.agent.tools.create_video.time.sleep")
    @patch("api.agent.tools.create_video.get_create_video_generation_llm_configs")
    @patch("api.agent.tools.create_video.resolve_export_target")
    def test_polling_status_checks_do_not_log_extra_completions(
        self,
        mock_resolve,
        mock_configs,
        mock_sleep,
        mock_litellm,
        mock_log_completion,
        mock_write,
        mock_signed_url,
        mock_set_var,
    ):
        mock_resolve.return_value = ("/exports/test.mp4", False, None)
        mock_configs.return_value = [_make_config(model="ltx/ltx-2-3-fast")]
        pending_obj = _make_video_obj(status="queued")
        completed_obj = _make_video_obj(status="completed")
        mock_litellm.video_generation.return_value = pending_obj
        mock_litellm.video_status.return_value = completed_obj
        mock_litellm.video_content.return_value = b"video-data"
        mock_write.return_value = {"status": "ok", "path": "/exports/test.mp4", "node_id": "node-123"}

        agent = MagicMock()
        agent.id = "agent-123"
        result = execute_create_video(agent, {"prompt": "a sunset", "file_path": "/exports/test.mp4"})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(mock_litellm.video_status.call_count, 1)
        mock_log_completion.assert_called_once()
        self.assertEqual(
            mock_log_completion.call_args.kwargs["completion_type"],
            PersistentAgentCompletion.CompletionType.VIDEO_GENERATION,
        )
