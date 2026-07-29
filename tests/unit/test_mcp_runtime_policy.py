from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.models import MCPServerConfig
from api.services.mcp_runtime_policy import (
    mcp_server_is_stdio,
    mcp_server_requires_agent_sandbox,
)


@tag("batch_mcp_tools")
class MCPRuntimePolicyTests(SimpleTestCase):
    def test_transport_and_sandbox_policy_across_runtime_representations(self):
        cases = [
            ("missing", None, False, False),
            (
                "user stdio object",
                SimpleNamespace(
                    scope=MCPServerConfig.Scope.USER,
                    command="npx",
                    url="",
                ),
                True,
                True,
            ),
            (
                "organization stdio payload",
                {
                    "scope": MCPServerConfig.Scope.ORGANIZATION,
                    "command": "npx",
                    "url": "",
                },
                True,
                True,
            ),
            (
                "platform stdio",
                {
                    "scope": MCPServerConfig.Scope.PLATFORM,
                    "command": "npx",
                    "url": "",
                },
                True,
                False,
            ),
            (
                "user http",
                {
                    "scope": MCPServerConfig.Scope.USER,
                    "command": "",
                    "url": "https://example.com/mcp",
                },
                False,
                False,
            ),
            (
                "url takes transport precedence",
                {
                    "scope": MCPServerConfig.Scope.USER,
                    "command": "npx",
                    "url": "https://example.com/mcp",
                },
                False,
                False,
            ),
        ]

        for label, runtime, expected_stdio, expected_sandbox in cases:
            with self.subTest(label):
                self.assertEqual(mcp_server_is_stdio(runtime), expected_stdio)
                self.assertEqual(
                    mcp_server_requires_agent_sandbox(runtime),
                    expected_sandbox,
                )
