from typing import Any

from django.test import SimpleTestCase, tag
from unittest.mock import patch


@tag('batch_browser_agent_temperature')
class BrowserAgentTemperatureTests(SimpleTestCase):
    def _run_failover(self, entry_overrides: dict[str, Any]) -> list[dict[str, Any]]:
        from api.tasks.browser_agent_tasks import _execute_agent_with_failover

        calls: list[dict[str, Any]] = []

        async def fake_run_agent(*_args: Any, **kwargs: Any):
            calls.append(kwargs)
            return "ok", {"total_tokens": 0}

        entry = {
            'provider_key': 'openai',
            'endpoint_key': 'test-endpoint',
            'weight': 1.0,
            'browser_model': 'gpt-test',
            'base_url': '',
            'backend': 'OPENAI',
            'supports_vision': True,
            'max_output_tokens': None,
            'api_key': 'sk-test',
            **entry_overrides,
        }

        with patch('api.tasks.browser_agent_tasks._run_agent', new=fake_run_agent):
            _execute_agent_with_failover(
                task_input="hello",
                task_id="task-123",
                provider_priority=[[entry]],
                proxy_server=None,
                controller=None,
                sensitive_data=None,
                output_schema=None,
                browser_use_agent_id=None,
                persistent_agent_id=None,
            )

        self.assertEqual(len(calls), 1)
        return calls

    def test_supports_temperature_defaults_to_true(self) -> None:
        calls = self._run_failover({})
        self.assertTrue(calls[0].get('supports_temperature'))
        self.assertTrue(calls[0].get('extraction_supports_temperature'))

    def test_supports_temperature_false_passed_to_run_agent(self) -> None:
        calls = self._run_failover({
            'supports_temperature': False,
            'extraction': {
                'provider_key': 'openai',
                'browser_model': 'gpt-extract',
                'api_key': 'sk-test',
                'supports_temperature': False,
            },
        })
        self.assertFalse(calls[0].get('supports_temperature'))
        self.assertFalse(calls[0].get('extraction_supports_temperature'))

    def test_init_chat_llm_omits_temperature_when_unsupported(self) -> None:
        from api.tasks.browser_agent_tasks import _init_chat_llm

        common = dict(backend='OPENAI', provider_key='openai', api_key='sk-test', model_name='gpt-test', base_url=None)
        self.assertEqual(_init_chat_llm(**common).temperature, 0)
        self.assertIsNone(_init_chat_llm(**common, supports_temperature=False).temperature)
