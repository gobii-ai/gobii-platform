import json

from django.test import SimpleTestCase, tag

from api.agent.tools.json_digest import digest


@tag("batch_result_analysis")
class JsonDigestTests(SimpleTestCase):
    def test_array_of_objects_root(self):
        data = [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
            {"id": 3, "name": "Gamma"},
        ]
        result = digest(data, raw_json=json.dumps(data))

        self.assertEqual(result.root_type, "array_of_objects")
        self.assertGreaterEqual(result.array_consistency, 0.8)
        self.assertIn("structured", result.verdict)

    def test_sparsity_detected(self):
        data = [
            {"id": 1, "value": None, "extra": None},
            {"id": 2, "value": "", "extra": None},
            {"id": 3, "value": None, "extra": None},
        ]
        result = digest(data)

        self.assertEqual(result.sparsity_verdict, "very_sparse")
        self.assertGreaterEqual(result.sparsity, 0.6)

    def test_key_convention_snake_case(self):
        data = {"first_name": "Ada", "last_name": "Lovelace", "user_id": 1}
        result = digest(data)

        self.assertEqual(result.key_convention, "snake_case")
        self.assertEqual(result.key_style, "semantic")
