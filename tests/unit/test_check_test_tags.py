from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, tag

from scripts.check_test_tags import collect_cross_shard_tests, load_ci_tag_shards


@tag("complexity_guardrails_batch")
class CheckTestTagsTests(SimpleTestCase):
    def test_class_and_method_tags_cannot_cross_shards(self):
        source = """
from django.test import TestCase, tag

@tag("batch_one")
class ExampleTests(TestCase):
    @tag("batch_two")
    def test_example(self):
        pass
"""
        with TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "test_example.py"
            test_file.write_text(source, encoding="utf-8")

            conflicts = collect_cross_shard_tests(
                str(test_file),
                {"batch_one": "shard_01", "batch_two": "shard_02"},
            )

        self.assertEqual(len(conflicts), 1)
        self.assertIn("ExampleTests.test_example", conflicts[0][0])

    def test_multiple_tags_in_one_shard_are_allowed(self):
        source = """
from django.test import TestCase, tag

@tag("batch_one")
class ExampleTests(TestCase):
    @tag("batch_two")
    def test_example(self):
        pass
"""
        with TemporaryDirectory() as temp_dir:
            test_file = Path(temp_dir) / "test_example.py"
            test_file.write_text(source, encoding="utf-8")

            conflicts = collect_cross_shard_tests(
                str(test_file),
                {"batch_one": "shard_01", "batch_two": "shard_01"},
            )

        self.assertEqual(conflicts, [])

    def test_ci_matrix_tags_are_mapped_to_their_shards(self):
        workflow = """
jobs:
  tests:
    strategy:
      matrix:
        include:
          - batch: shard_01
            tag: batch_one batch_two
          - batch: shard_02
            tag: batch_three
"""
        with TemporaryDirectory() as temp_dir:
            workflow_file = Path(temp_dir) / "ci.yml"
            workflow_file.write_text(workflow, encoding="utf-8")

            tag_shards = load_ci_tag_shards(str(workflow_file))

        self.assertEqual(
            tag_shards,
            {
                "batch_one": "shard_01",
                "batch_two": "shard_01",
                "batch_three": "shard_02",
            },
        )

    def test_ci_tag_cannot_be_assigned_to_multiple_shards(self):
        workflow = """
jobs:
  tests:
    strategy:
      matrix:
        include:
          - batch: shard_01
            tag: batch_one batch_two
          - batch: shard_02
            tag: batch_one batch_three
"""
        with TemporaryDirectory() as temp_dir:
            workflow_file = Path(temp_dir) / "ci.yml"
            workflow_file.write_text(workflow, encoding="utf-8")

            with self.assertRaisesMessage(
                ValueError,
                "CI tag 'batch_one' is assigned to both shard_01 and shard_02",
            ):
                load_ci_tag_shards(str(workflow_file))
