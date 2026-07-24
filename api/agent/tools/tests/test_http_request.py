from django.test import SimpleTestCase, tag

from api.agent.tools.http_request import _native_api_error_message, get_http_request_tool


@tag("http_request_batch")
class NativeHttpErrorMessageTests(SimpleTestCase):
    def test_tool_description_prefers_dollar_secret_placeholders(self):
        description = get_http_request_tool()["function"]["description"]

        self.assertIn("$[secret:my_api_key]", description)
        self.assertIn("<<<my_api_key>>>", description)

    def test_extracts_google_error_message(self):
        message = _native_api_error_message(
            {
                "error": {
                    "code": 400,
                    "message": "Unable to parse range: Sheet1",
                    "status": "INVALID_ARGUMENT",
                }
            }
        )

        self.assertEqual(message, "Unable to parse range: Sheet1")

    def test_extracts_top_level_error_description(self):
        message = _native_api_error_message({"error_description": "Token expired."})

        self.assertEqual(message, "Token expired.")

    def test_truncates_large_string_error_body(self):
        message = _native_api_error_message("x" * 2000)

        self.assertLess(len(message), 1300)
        self.assertTrue(message.endswith("[truncated]"))
