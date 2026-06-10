from django.test import SimpleTestCase, tag

from api.agent.core.prompt_context import _get_formatting_guidance
from api.agent.tools.email_sender import get_send_email_tool
from api.agent.tools.web_chat_sender import get_send_chat_tool


@tag("batch_promptree")
class FormattingGuidanceOtherChannelTests(SimpleTestCase):
    def test_guidance_includes_all_delivery_surfaces(self):
        guidance = _get_formatting_guidance()
        self.assertIn("<web_chat>", guidance)
        self.assertIn("<email>", guidance)
        self.assertIn("<sms>", guidance)
        self.assertIn("<fallback>", guidance)

    def test_guidance_no_longer_emits_active_channel(self):
        guidance = _get_formatting_guidance()
        self.assertNotIn("<active_channel>", guidance)

    def test_guidance_preserves_row_level_links(self):
        guidance = _get_formatting_guidance()
        self.assertIn("Preserve row/entity URLs", guidance)
        self.assertIn("url/link/source_url/listing_url/detail_url", guidance)
        self.assertIn("add a Link column", guidance)

    def test_send_tool_descriptions_preserve_row_level_links(self):
        chat_tool = get_send_chat_tool()
        email_tool = get_send_email_tool()
        chat_description = chat_tool["function"]["parameters"]["properties"]["body"]["description"]
        email_description = email_tool["function"]["parameters"]["properties"]["mobile_first_html"]["description"]

        for description in (chat_description, email_description):
            self.assertIn("url/link/source_url/listing_url/detail_url", description)
            self.assertIn("Link column", description)
