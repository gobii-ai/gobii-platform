from unittest import mock

from django.test import TestCase, tag

from agents.services import AIEmployeeTemplateService


class AIEmployeeScheduleLogicTests(TestCase):
    @tag("batch_schedule")
    def test_schedule_jitter_applies_offset(self):
        with mock.patch('agents.services.random.randint', return_value=7):
            jittered = AIEmployeeTemplateService.compute_schedule_with_jitter("0 10 * * *", 10)
        self.assertEqual(jittered, "7 10 * * *")

    @tag("batch_schedule")
    def test_schedule_jitter_no_change_when_disabled(self):
        unchanged = AIEmployeeTemplateService.compute_schedule_with_jitter("15 9 * * MON-FRI", 0)
        self.assertEqual(unchanged, "15 9 * * MON-FRI")


class AIEmployeeTemplateServiceTests(TestCase):
    @tag("batch_schedule")
    def test_get_template_by_code_returns_fresh_copy(self):
        template = AIEmployeeTemplateService.get_template_by_code("sales-pipeline-whisperer")
        self.assertIsNotNone(template)
        self.assertEqual(template.display_name, "Pipeline Whisperer")

        # Mutate and confirm future lookups are not impacted.
        template.display_name = "Modified"
        fresh = AIEmployeeTemplateService.get_template_by_code("sales-pipeline-whisperer")
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh.display_name, "Pipeline Whisperer")
