import re
from typing import Set
from urllib.parse import urlsplit

from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import EvalRunTask, PersistentAgentMessage


@register_scenario
class JobListingsBundledReplyScenario(EvalScenario, ScenarioExecutionTools):
    slug = "job_listings_bundled_reply"
    description = (
        "Ensures the agent pulls three listings (one per role) and sends them together "
        "instead of replying once per listing."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_three_sources", assertion_type="manual"),
        ScenarioTask(name="verify_bundled_reply", assertion_type="manual"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        # Send the job scraping prompt and wait for processing to finish.
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")

        prompt = (
            "Find three current remote Full Stack Software Engineer job listings from three different sources."
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                prompt,
                trigger_processing=True,
                eval_run_id=run_id,
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        # Find the job-bearing outbound message and ensure it has 3 sources.
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_three_sources")

        outbound = list(
            PersistentAgentMessage.objects.filter(
                owner_agent_id=agent_id,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            ).order_by("timestamp")
        )

        job_messages = []
        for msg in outbound:
            if self._is_job_message(msg.body or ""):
                job_messages.append(msg)

        if not job_messages:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_three_sources",
                observed_summary="No outbound message contained job listings.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary="No outbound job message to evaluate.",
            )
            return

        first_message = job_messages[0]
        domains = self._extract_domains(first_message.body or "")
        job_item_count = self._estimate_job_item_count(first_message.body or "")

        if job_item_count < 3 or len(domains) < 3:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_three_sources",
                observed_summary=(
                    f"Found {job_item_count} job items and {len(domains)} unique sources; expected at least 3 of each."
                ),
                artifacts={"message": first_message},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_three_sources",
                observed_summary=f"Detected at least 3 listings across {len(domains)} sources: {', '.join(sorted(domains))[:150]}",
                artifacts={"message": first_message},
            )

        if len(job_messages) > 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary=(
                    f"Job details split across {len(job_messages)} messages; expected a single bundled reply."
                ),
                artifacts={"message": first_message},
            )
            return

        if missing_jobs:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary=f"Bundled reply missing: {', '.join(missing_jobs)}.",
                artifacts={"message": first_message},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_bundled_reply",
            observed_summary="Job listings bundled into a single outbound message.",
            artifacts={"message": first_message},
        )

    @staticmethod
    def _extract_domains(body: str) -> Set[str]:
        domains: Set[str] = set()
        for match in re.findall(r"https?://[^\s)>\]]+", body or ""):
            host = urlsplit(match).netloc.split(":")[0]
            if host:
                domains.add(host.lower())
        return domains

    @staticmethod
    def _estimate_job_item_count(body: str) -> int:
        lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
        bullet_like = [
            line
            for line in lines
            if line.startswith(("-", "*", "•"))
            or line.split(" ", 1)[0].rstrip(".").isdigit()
        ]
        url_count = (body or "").lower().count("http")
        return max(len(bullet_like), url_count)

    @staticmethod
    def _is_job_message(body: str) -> bool:
        text = (body or "").lower()
        if not text.strip():
            return False
        url_count = text.count("http")
        keyword_hits = sum(
            1
            for kw in ("full stack", "software engineer", "job", "opening", "role", "position", "apply")
            if kw in text
        )
        bullet_like = sum(
            1
            for line in (body or "").splitlines()
            if line.strip().startswith(("-", "*", "•"))
            or line.strip().split(" ", 1)[0].rstrip(".").isdigit()
        )
        return url_count >= 2 or bullet_like >= 3 or (keyword_hits >= 3 and len(text) > 80)
