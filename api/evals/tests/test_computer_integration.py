from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.system_skills.defaults import COMPUTER_SYSTEM_SKILL, _computer_prompt_context
from api.evals.scenarios.computer_integration import (
    COMPUTER_INTEGRATION_CASES,
    ComputerIntegrationScenario,
)
from api.models import BrowserUseAgent, PersistentAgent


@tag("eval_sim")
class ComputerIntegrationEvalSetupTests(TestCase):
    def test_missing_connection_fixture_exposes_pairing_guidance(self):
        user = get_user_model().objects.create_user(username="computer-eval@example.test")
        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser = BrowserUseAgent.objects.create(user=user, name="Computer eval browser")
            agent = PersistentAgent.objects.create(
                user=user,
                name="Computer eval",
                charter="",
                browser_use_agent=browser,
                execution_environment="eval",
            )
        case = next(case for case in COMPUTER_INTEGRATION_CASES if case.slug == "computer_missing_connection")
        scenario = ComputerIntegrationScenario()
        scenario.case = case

        scenario._prepare_agent(agent)

        self.assertTrue(COMPUTER_SYSTEM_SKILL.should_render_prompt(agent))
        self.assertEqual(_computer_prompt_context(agent), "Connected computer state: none.")
