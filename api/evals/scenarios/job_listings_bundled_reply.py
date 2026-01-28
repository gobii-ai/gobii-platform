import re
import time
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
        ScenarioTask(name="verify_three_sources", assertion_type="llm_judge"),
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

        def fetch_outbound():
            return list(
                PersistentAgentMessage.objects.filter(
                    owner_agent_id=agent_id,
                    is_outbound=True,
                    timestamp__gt=inbound.timestamp,
                ).order_by("timestamp")
            )

        outbound = fetch_outbound()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if any(self._is_job_message(msg.body or "") for msg in outbound):
                break
            time.sleep(2)
            outbound = fetch_outbound()

        first_three = outbound[:3]
        job_messages_judged = [msg for msg in first_three if self._is_job_message(msg.body or "")]

        if not first_three:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_three_sources",
                observed_summary="No first/second/third outbound message to judge for job listings.",
            )
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary="No outbound messages to evaluate for bundled listings.",
            )
            return

        judged_results = []
        for idx, msg in enumerate(first_three, start=1):
            domains = self._extract_domains(msg.body or "")
            job_item_count = self._estimate_job_item_count(msg.body or "")

            judge_choice, judge_reason = self.llm_judge(
                question=(
                    "A user asked for three remote Full Stack Software Engineer job listings from three different sources, "
                    "combined into one reply. Does the assistant message provide three distinct listings and clearly cite "
                    "three different sources or links within this single message?"
                ),
                context=(
                    f"Message {idx}: Detected {job_item_count} possible job items and {len(domains)} unique domains: "
                    f"{', '.join(sorted(domains))}.\n\nAssistant reply:\n{msg.body or ''}"
                ),
                options=["Pass", "Fail"],
            )
            judged_results.append((msg, judge_choice, judge_reason))

        pass_results = [(msg, reason) for msg, choice, reason in judged_results if choice == "Pass"]
        if pass_results:
            passed_message, pass_reason = pass_results[0]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_three_sources",
                observed_summary=f"LLM judge: Pass on message with bundled listings. Reasoning: {pass_reason}",
                artifacts={"message": passed_message},
            )
        else:
            combined_reasons = "; ".join([reason for _, _, reason in judged_results])
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_three_sources",
                observed_summary=f"LLM judge: No message contained bundled listings. Reasons: {combined_reasons}",
                artifacts={"messages": [msg for msg, _, _ in judged_results]},
            )

        if len(outbound) > 3:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary=(
                    f"Too many outbound messages ({len(outbound)}); expected intro, searching, and bundled listings."
                ),
                artifacts={"message": pass_results[0][0] if pass_results else outbound[-1]},
            )
            return

        combined_job_msg_count = max(len(pass_results), len(job_messages_judged))
        if combined_job_msg_count > 1:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary=(
                    f"Job details split across {combined_job_msg_count} messages; expected a single bundled reply."
                ),
                artifacts={
                    "message": (
                        pass_results[0][0]
                        if pass_results
                        else (job_messages_judged[0] if job_messages_judged else outbound[-1])
                    )
                },
            )
            return

        if pass_results:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bundled_reply",
                observed_summary="Job listings bundled into a single outbound message.",
                artifacts={"message": pass_results[0][0]},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bundled_reply",
                observed_summary="No judged message contained bundled job listings.",
                artifacts={"messages": [msg for msg, _, _ in judged_results]},
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
