from unittest.mock import patch, MagicMock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, tag

# Unit under test
from util.phone import validate_and_format_e164


@tag("batch_sms")
class PhoneRegionValidationTests(SimpleTestCase):
    @patch("phonenumbers.format_number")
    @patch("phonenumbers.region_code_for_number")
    @patch("phonenumbers.is_valid_number")
    @patch("phonenumbers.parse")
    def test_valid_number_supported_region_formats_e164(
        self, mock_parse, mock_is_valid, mock_region, mock_format
    ):
        # Arrange: mock a successful parse and region that we support (e.g., US)
        parsed_obj = MagicMock()
        mock_parse.return_value = parsed_obj
        mock_is_valid.return_value = True
        mock_region.return_value = "US"  # In SUPPORTED_REGION_CODES
        mock_format.return_value = "+14155552671"

        # Act
        result = validate_and_format_e164("+1 (415) 555-2671")

        # Assert
        self.assertEqual(result, "+14155552671")
        mock_parse.assert_called_once()
        mock_is_valid.assert_called_once_with(parsed_obj)
        mock_region.assert_called_once_with(parsed_obj)
        mock_format.assert_called_once()

    @patch("phonenumbers.is_valid_number", return_value=False)
    @patch("phonenumbers.parse")
    def test_invalid_number_raises_invalid_phone(self, mock_parse, _mock_is_valid):
        parsed_obj = MagicMock()
        mock_parse.return_value = parsed_obj

        with self.assertRaises(ValidationError) as ctx:
            validate_and_format_e164("not-a-number")

        self.assertEqual(ctx.exception.code, "invalid_phone")

    @patch("phonenumbers.region_code_for_number", return_value="CN")  # Not in supported list
    @patch("phonenumbers.is_valid_number", return_value=True)
    @patch("phonenumbers.parse")
    def test_unsupported_region_raises_specific_error(self, mock_parse, _mock_is_valid, _mock_region):
        parsed_obj = MagicMock()
        mock_parse.return_value = parsed_obj

        with self.assertRaises(ValidationError) as ctx:
            validate_and_format_e164("+86 10 8555 1234")

        self.assertEqual(ctx.exception.code, "unsupported_region")

    def test_empty_input_raises_invalid_phone(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_and_format_e164("")
        self.assertEqual(ctx.exception.code, "invalid_phone")

    @patch("phonenumbers.parse", side_effect=Exception("parse failure"))
    def test_parser_exception_maps_to_invalid_phone(self, _mock_parse):
        with self.assertRaises(ValidationError) as ctx:
            validate_and_format_e164("+999999999")
        self.assertEqual(ctx.exception.code, "invalid_phone")

