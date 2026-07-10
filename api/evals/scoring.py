from dataclasses import dataclass
from typing import Iterable

from api.models import EvalRun, EvalRunTask


SCENARIO_PASSED = "passed"
SCENARIO_FAILED = "failed"
SCENARIO_PENDING = "pending"
SCENARIO_UNSCORED = "unscored"
SCORING_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ScenarioOutcome:
    status: str
    scored_requirements: int
    passed_requirements: int
    failed_requirements: int

    @property
    def passed(self) -> bool:
        return self.status == SCENARIO_PASSED

    def as_dict(self) -> dict[str, int | str | bool]:
        return {
            "status": self.status,
            "passed": self.passed,
            "scored_requirements": self.scored_requirements,
            "passed_requirements": self.passed_requirements,
            "failed_requirements": self.failed_requirements,
            "scoring_schema_version": SCORING_SCHEMA_VERSION,
        }


def evaluate_scenario_tasks(
    tasks: Iterable[EvalRunTask],
    *,
    run_status: str | None = None,
) -> ScenarioOutcome:
    scored_tasks = [task for task in tasks if task.is_scored]
    passed = sum(task.status == EvalRunTask.Status.PASSED for task in scored_tasks)
    failed = sum(
        task.status in (
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        )
        for task in scored_tasks
    )

    if run_status == EvalRun.Status.ERRORED:
        status = SCENARIO_FAILED
    elif not scored_tasks:
        status = SCENARIO_UNSCORED
    elif failed:
        status = SCENARIO_FAILED
    elif passed == len(scored_tasks):
        status = SCENARIO_PASSED
    else:
        status = SCENARIO_PENDING

    return ScenarioOutcome(
        status=status,
        scored_requirements=len(scored_tasks),
        passed_requirements=passed,
        failed_requirements=failed,
    )


def evaluate_run(run: EvalRun) -> ScenarioOutcome:
    return evaluate_scenario_tasks(run.tasks.all(), run_status=run.status)


def summarize_runs(runs: Iterable[EvalRun]) -> dict[str, int | float | None]:
    outcomes = [evaluate_run(run) for run in runs]
    passed = sum(outcome.status == SCENARIO_PASSED for outcome in outcomes)
    failed = sum(outcome.status == SCENARIO_FAILED for outcome in outcomes)
    pending = sum(outcome.status == SCENARIO_PENDING for outcome in outcomes)
    unscored = sum(outcome.status == SCENARIO_UNSCORED for outcome in outcomes)
    completed = passed + failed
    return {
        "total": len(outcomes),
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "unscored": unscored,
        "pass_rate": passed / completed if completed else None,
        "scoring_schema_version": SCORING_SCHEMA_VERSION,
    }
