from django.test import SimpleTestCase, tag

from api.agent.core.prompt_context import _get_formatting_guidance


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
