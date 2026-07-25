"""The rejection message for link handles in code must name the remedy that actually works.

Handles are deliberately rejected in code-bearing fields. The raw URL is recoverable from
__tool_results and a literal URL is accepted there, but the message never said so, so an agent
that hit this had no way to learn the way out.
"""
from django.test import SimpleTestCase, tag

from api.agent.core.link_references import LinkReferenceResolutionError, resolve_link_reference_params


class _Agent:
    id = "00000000-0000-0000-0000-000000000001"


@tag("batch_event_processing")
class LinkReferenceRemedyMessageTests(SimpleTestCase):
    def _rejection_message(self, tool_name: str, params: dict) -> str:
        with self.assertRaises(LinkReferenceResolutionError) as caught:
            resolve_link_reference_params(params, _Agent(), tool_name=tool_name)
        return str(caught.exception)

    def test_code_field_rejection_points_at_source_extraction(self):
        for tool_name, params in (
            ("create_custom_tool", {"source_code": 'URL = "$[link:LABCDEFGHJKMNPQRS]"'}),
            ("apply_patch", {"patch": '+URL = "$[link:LABCDEFGHJKMNPQRS]"'}),
        ):
            with self.subTest(tool_name=tool_name):
                message = self._rejection_message(tool_name, params)
                self.assertIn("__tool_results", message)
                self.assertIn("raw URL", message)

    def test_rejection_still_identifies_the_offending_parameter(self):
        message = self._rejection_message(
            "create_custom_tool", {"source_code": 'URL = "$[link:LABCDEFGHJKMNPQRS]"'}
        )

        self.assertIn("create_custom_tool.source_code", message)
        self.assertIn("Query not executed", message)
