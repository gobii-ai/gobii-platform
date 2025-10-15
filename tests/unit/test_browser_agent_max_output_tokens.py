from typing import Any

from django.test import SimpleTestCase, tag
from unittest.mock import patch


@tag('batch_browser_agent_max_tokens')
class BrowserAgentMaxOutputTokensTests(SimpleTestCase):
    def test_override_passed_to_run_agent(self) -> None:
        from api.tasks.browser_agent_tasks import _execute_agent_with_failover

        calls: list[dict[str, Any]] = []

        async def fake_run_agent(*_args: Any, **kwargs: Any):
            calls.append(kwargs)
            return "ok", {"total_tokens": 0}

        provider_priority = [[{
            'provider_key': 'openai',
            'endpoint_key': 'test-endpoint',
            'weight': 1.0,
            'browser_model': 'gpt-test',
            'base_url': '',
            'backend': 'OPENAI',
            'supports_vision': True,
            'max_output_tokens': 1024,
            'api_key': 'sk-test',
        }]]

        with patch('api.tasks.browser_agent_tasks._run_agent', new=fake_run_agent):
            result, usage = _execute_agent_with_failover(
                task_input="hello",
                task_id="task-123",
                provider_priority=provider_priority,
                proxy_server=None,
                controller=None,
                sensitive_data=None,
                output_schema=None,
                browser_use_agent_id=None,
                persistent_agent_id=None,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(usage, {"total_tokens": 0})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get('override_max_output_tokens'), 1024)
