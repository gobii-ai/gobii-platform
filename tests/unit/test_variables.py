import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core.variables import (
    extract_variableize_config,
    generate_variable_name,
    resolve_variables_in_params,
    variableize_from_config,
)
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentToolCall,
    PersistentAgentVariable,
)

User = get_user_model()


@tag("batch_event_processing")
class VariableHelpersTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="var-user",
            email="var@example.com",
            password="testpass123",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=self.browser_agent,
            name="Agent",
            charter="charter",
        )

    def test_resolve_variables_in_params_replaces_reference(self):
        payload = {"items": [1, 2, 3]}
        value_text = json.dumps(payload)
        PersistentAgentVariable.objects.create(
            agent=self.agent,
            name="payload_cache",
            value=value_text,
            is_json=True,
            size_bytes=len(value_text.encode("utf-8")),
        )

        params = {"users": "$payload_cache", "extra": "keep"}
        resolved, used = resolve_variables_in_params(self.agent, params)

        self.assertEqual(resolved["users"], {"items": [1, 2, 3]})
        self.assertEqual(resolved["extra"], "keep")
        self.assertIn("payload_cache", used)

    def test_variableize_from_config_creates_variables(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="call")
        tool_call = PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="send_email",
            tool_params={"body": "hello"},
            result="{}",
        )

        result = {
            "subject": "Hi",
            "body": "Hello world",
            "_variableize": {"fields": ["body"]},
        }
        cleaned, config = extract_variableize_config(result)
        self.assertNotIn("_variableize", cleaned)

        created = variableize_from_config(self.agent, tool_call, cleaned, config)

        self.assertEqual(len(created), 1)
        expected_name = generate_variable_name(tool_call, field="body")
        self.assertEqual(created[0].name, expected_name)
        self.assertEqual(created[0].value, "Hello world")
        self.assertFalse(created[0].is_json)
