from django.test import SimpleTestCase, tag

from api.services.mcp_remote_runtime import (
    REMOTE_MCP_REMOTE_PACKAGE,
    is_mcp_remote_invocation,
    rewrite_mcp_remote_invocation,
)


@tag("batch_console_mcp_servers")
class MCPRemoteRuntimeTests(SimpleTestCase):
    def test_detects_mcp_remote_variants(self):
        cases = [
            ("mcp-remote", ["https://remote.example.com/sse"]),
            ("npx", ["mcp-remote", "https://remote.example.com/sse"]),
            ("npx", ["-y", "mcp-remote@latest", "https://remote.example.com/sse"]),
            ("npm", ["exec", "mcp-remote", "https://remote.example.com/sse"]),
            ("pnpm", ["dlx", "mcp-remote", "https://remote.example.com/sse"]),
            ("npx", ["-p", "mcp-remote", "mcp-remote", "https://remote.example.com/sse"]),
            ("npx", ["-p", REMOTE_MCP_REMOTE_PACKAGE, REMOTE_MCP_REMOTE_PACKAGE, "https://remote.example.com/sse"]),
        ]
        for command, args in cases:
            with self.subTest(command=command, args=args):
                self.assertTrue(is_mcp_remote_invocation(command, args))

    def test_ignores_non_remote_commands(self):
        self.assertFalse(is_mcp_remote_invocation("node", ["server.js"]))
        self.assertFalse(is_mcp_remote_invocation("npx", ["@modelcontextprotocol/server-github"]))

    def test_rewrites_remote_invocation_to_bridge_package(self):
        rewritten = rewrite_mcp_remote_invocation(
            "npx",
            ["-y", "mcp-remote", "https://remote.example.com/sse", "--transport", "http-first"],
        )

        self.assertTrue(rewritten.is_remote_mcp_remote)
        self.assertEqual(rewritten.command, "npx")
        self.assertEqual(rewritten.args[0], "-y")
        self.assertEqual(rewritten.args[1], REMOTE_MCP_REMOTE_PACKAGE)
        self.assertIn("https://remote.example.com/sse", rewritten.args)
        self.assertIn("--transport", rewritten.args)
