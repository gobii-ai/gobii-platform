from api.evals.base import EvalScenario, ScenarioTask
from api.evals.registry import register_scenario
from api.evals.execution import ScenarioExecutionTools
from api.models import (
    BrowserUseAgentTask,
    EvalRunTask,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)

LINKEDIN_TOOLS = [
    "mcp_brightdata_web_data_linkedin_person_profile",
    "mcp_brightdata_web_data_linkedin_company_profile",
    "mcp_brightdata_web_data_linkedin_job_listings",
    "mcp_brightdata_web_data_linkedin_posts",
    "mcp_brightdata_web_data_linkedin_people_search",
]

BROWSER_TOOLS = [
    "spawn_web_task",
    "scraping_browser_navigate",
    "scraping_browser_snapshot",
    "scraping_browser_click_ref",
    "scraping_browser_type_ref",
    "scraping_browser_scroll",
    "scraping_browser_scroll_to_ref",
    "scraping_browser_wait_for_ref",
]


@register_scenario
class LinkedInToolPreferenceScenario(EvalScenario, ScenarioExecutionTools):
    slug = "linkedin_prefers_brightdata"
    description = "Ensures LinkedIn lookups rely on BrightData LinkedIn tools instead of spawning browser tasks."
    tasks = [
        ScenarioTask(name="inject_linkedin_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_linkedin_tool_usage", assertion_type="manual"),
        ScenarioTask(name="verify_no_browser_tasks_before_linkedin", assertion_type="manual"),
        ScenarioTask(name="verify_no_browser_tasks", assertion_type="manual"),
        ScenarioTask(name="verify_linkedin_response", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # 1) Send LinkedIn lookup request with mocks to keep execution deterministic
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_linkedin_prompt",
        )

        mock_config = self._build_mock_config()
        with self.wait_for_agent_idle(agent_id, timeout=120):
            prompt = (
                "I need the LinkedIn profile for Will Bonde, a senior software engineer at Gobii. Pull his headline and location."
            )
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_linkedin_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        # 2) Check that a LinkedIn BrightData tool was used
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_linkedin_tool_usage",
        )

        linkedin_calls = PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name__in=LINKEDIN_TOOLS,
        ).order_by("step__created_at")

        first_linkedin_call = linkedin_calls.first()
        if first_linkedin_call:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_linkedin_tool_usage",
                observed_summary=f"Agent used LinkedIn tool: {first_linkedin_call.tool_name}",
                artifacts={"step": first_linkedin_call.step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_linkedin_tool_usage",
                observed_summary="No BrightData LinkedIn tool call found for the request.",
            )

        # 3) Ensure no browser task/tool was used before the first LinkedIn call
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_browser_tasks_before_linkedin",
        )

        if first_linkedin_call:
            browser_before = PersistentAgentToolCall.objects.filter(
                step__eval_run_id=run_id,
                step__created_at__gte=inbound.timestamp,
                step__created_at__lt=first_linkedin_call.step.created_at,
                tool_name__in=BROWSER_TOOLS,
            )
            if browser_before.exists():
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="verify_no_browser_tasks_before_linkedin",
                    observed_summary=f"Browsering before LinkedIn: {browser_before.count()} calls.",
                )
            else:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.PASSED,
                    task_name="verify_no_browser_tasks_before_linkedin",
                    observed_summary="No browser calls before LinkedIn tool.",
                )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_browser_tasks_before_linkedin",
                observed_summary="No LinkedIn call; cannot verify ordering.",
            )

        # 4) Ensure no browser task was spawned at all
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_browser_tasks",
        )

        browser_calls = PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name__in=BROWSER_TOOLS,
        )
        browser_tasks = BrowserUseAgentTask.objects.filter(eval_run_id=run_id)

        if browser_calls.exists() or browser_tasks.exists():
            details = []
            if browser_calls.exists():
                details.append(f"browser tool calls: {browser_calls.count()}")
            if browser_tasks.exists():
                details.append(f"browser tasks: {browser_tasks.count()}")
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_browser_tasks",
                observed_summary="; ".join(details) or "Browser task detected.",
                artifacts={"browser_task": browser_tasks.first()} if browser_tasks.exists() else {},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_browser_tasks",
                observed_summary="No browser tasks or browser tool calls detected.",
            )

        # 5) Validate the agent reply includes LinkedIn details
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_linkedin_response",
        )

        response = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=True,
            timestamp__gt=inbound.timestamp,
        ).order_by("-timestamp").first()

        body_text = (response.body or "") if response else ""
        required_bits = [
            "will",
            "gobii",
            "software engineer",
            "germantown",
        ]
        if response and all(bit in body_text.lower() for bit in required_bits):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_linkedin_response",
                observed_summary="Reply includes name, company, headline, and location.",
                artifacts={"message": response},
            )
        else:
            preview = response.body if response else "No reply"
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_linkedin_response",
                observed_summary=f"Missing LinkedIn details in reply. Body: {preview}",
                artifacts={"message": response} if response else {},
            )

    @staticmethod
    def _build_mock_config() -> dict:
        """Provide deterministic tool outputs to keep the eval fast and offline."""
        return {
            # Encourage the agent to use LinkedIn data tools instead of browsing
            "spawn_web_task": {
                "status": "error",
                "message": "Browser tasks disabled for this eval; use LinkedIn data tools instead.",
            },
            "mcp_brightdata_web_data_linkedin_people_search": {
                "status": "ok",
                "results": [
                    {
                        "name": "Will Bonde",
                        "headline": "AI @ Gobii. Cancer survivor. Hoping to make the world better w/software.",
                        "location": "Germantown, Maryland, United States",
                        "url": "https://www.linkedin.com/in/willbonde",
                    }
                ],
            },
            "mcp_brightdata_web_data_linkedin_person_profile": {
                "status": "ok",
                "name": "Will Bonde",
                "headline": "AI @ Gobii. Cancer survivor. Hoping to make the world better w/software.",
                "location": "Germantown, Maryland, United States",
                "summary": "Senior Software Engineer at Gobii, passionate about leveraging AI to solve real-world problems.",
            },
            "mcp_brightdata_web_data_linkedin_company_profile": {
                "status": "ok",
                "name": "Gobii",
                "industry": "Software",
                "location": "San Francisco, CA",
            },
            "mcp_brightdata_web_data_linkedin_job_listings": {
                "status": "ok",
                "jobs": [
                    {
                        "title": "Senior Software Engineer",
                        "company": "Gobii",
                        "location": "Remote",
                    }
                ],
            },
            "mcp_brightdata_web_data_linkedin_posts": {
                "status": "ok",
                "posts": [
                    {
                        "author": "Will Bonde",
                        "text": "Senior Software Engineer at Gobii, passionate about leveraging AI to solve real-world problems.",
                    }
                ],
            },
        }
