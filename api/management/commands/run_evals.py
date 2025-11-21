import time
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.evals.tasks import run_eval_task, gc_eval_runs_task
from api.evals.runner import _update_suite_state
from api.models import BrowserUseAgent, EvalRun, EvalRunTask, EvalSuiteRun, PersistentAgent


class Command(BaseCommand):
    help = "Runs one or more eval suites (each suite runs its scenarios concurrently)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--suite",
            action="append",
            dest="suites",
            help="Suite slug to run (can be repeated). Defaults to 'all'.",
        )
        parser.add_argument(
            "--scenario",
            type=str,
            help="(Deprecated) Run a single scenario by slug. Prefer --suite.",
        )
        parser.add_argument(
            "--agent-id",
            type=str,
            help="UUID of an existing agent to reuse (only valid with --agent-strategy reuse_agent).",
        )
        parser.add_argument(
            "--agent-strategy",
            type=str,
            choices=[
                EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO,
                EvalSuiteRun.AgentStrategy.REUSE_AGENT,
            ],
            default=EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO,
            help="How agents are provisioned for the suite.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run synchronously (eager mode) for debugging.",
        )
        parser.add_argument(
            "--run-type",
            type=str,
            choices=[
                EvalSuiteRun.RunType.ONE_OFF,
                EvalSuiteRun.RunType.OFFICIAL,
            ],
            default=EvalSuiteRun.RunType.ONE_OFF,
            help="Label runs as one_off (default) or official for long-term tracking.",
        )
        parser.add_argument(
            "--official",
            action="store_true",
            help="Shortcut for --run-type official.",
        )

    def handle(self, *args, **options):
        suites_requested = options["suites"] or []
        scenario_slug = options["scenario"]
        agent_id = options["agent_id"]
        agent_strategy = options["agent_strategy"]
        sync_mode = options["sync"]
        run_type_option = options["run_type"]
        run_type = EvalSuiteRun.RunType.OFFICIAL if options["official"] else run_type_option

        if sync_mode:
            from django.conf import settings

            settings.CELERY_TASK_ALWAYS_EAGER = True
            settings.CELERY_TASK_EAGER_PROPAGATES = True
            self.stdout.write("Running in SYNCHRONOUS mode.")

        # Resolve suites
        suite_slugs: list[str] = suites_requested[:]
        if scenario_slug:
            self.stdout.write(self.style.WARNING("`--scenario` is deprecated; creating a one-off suite for it."))
            suite_slugs.append(f"single::{scenario_slug}")

        if not suite_slugs:
            suite_slugs = ["all"]

        suites = []
        for slug in suite_slugs:
            if slug.startswith("single::"):
                scenario_only = slug.split("single::", 1)[1]
                scenario = ScenarioRegistry.get(scenario_only)
                if not scenario:
                    raise CommandError(f"Scenario '{scenario_only}' not found.")
                suites.append(
                    (
                        slug,
                        [scenario.slug],
                        f"Ad-hoc suite for scenario {scenario.slug}",
                    )
                )
                continue

            suite_obj = SuiteRegistry.get(slug)
            if not suite_obj:
                raise CommandError(f"Suite '{slug}' not found.")
            suites.append((suite_obj.slug, list(suite_obj.scenario_slugs), suite_obj.description))

        if not suites:
            self.stdout.write(self.style.WARNING("No suites found to run."))
            return

        # Base user attribution
        User = get_user_model()
        user, _ = User.objects.get_or_create(username="eval_runner", defaults={"email": "eval@localhost"})

        def _create_ephemeral_agent(label_suffix: str) -> PersistentAgent:
            unique_id = f"{label_suffix}-{uuid.uuid4().hex[:8]}" if label_suffix else uuid.uuid4().hex[:12]
            browser_agent = BrowserUseAgent.objects.create(name=f"Eval Browser {unique_id}", user=user)
            agent = PersistentAgent.objects.create(
                name=f"Eval Agent {unique_id}",
                user=user,
                browser_use_agent=browser_agent,
                execution_environment="eval",
                charter="You are a test agent.",
            )
            return agent

        shared_agent: PersistentAgent | None = None
        if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT:
            if not agent_id:
                raise CommandError("--agent-id is required when agent-strategy is reuse_agent")
            try:
                shared_agent = PersistentAgent.objects.get(id=agent_id)
            except PersistentAgent.DoesNotExist:
                raise CommandError(f"Agent {agent_id} not found.")
            self.stdout.write(f"Using provided agent for reuse: {shared_agent.name} ({shared_agent.id})")

        suite_runs = []
        run_ids = []

        for suite_slug, scenario_slugs, description in suites:
            scenario_slugs = list(dict.fromkeys(scenario_slugs))
            suite_run = EvalSuiteRun.objects.create(
                suite_slug=suite_slug,
                initiated_by=user,
                status=EvalSuiteRun.Status.RUNNING,
                run_type=run_type,
                agent_strategy=agent_strategy,
                shared_agent=shared_agent if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT else None,
                started_at=timezone.now(),
            )

            self.stdout.write(self.style.SUCCESS(f"Created suite run {suite_run.id} ({suite_slug}) [{run_type}]"))

            created_for_suite = 0
            for scenario_slug in scenario_slugs:
                scenario = ScenarioRegistry.get(scenario_slug)
                if not scenario:
                    self.stdout.write(self.style.ERROR(f"Scenario '{scenario_slug}' missing; skipping."))
                    continue

                run_agent = shared_agent
                if agent_strategy == EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO or run_agent is None:
                    run_agent = _create_ephemeral_agent(label_suffix=scenario.slug[:8])
                    self.stdout.write(f"  Created ephemeral agent for {scenario.slug}: {run_agent.id}")

                run = EvalRun.objects.create(
                    suite_run=suite_run,
                    scenario_slug=scenario.slug,
                    scenario_version=getattr(scenario, "version", "") or "",
                    agent=run_agent,
                    initiated_by=user,
                    status=EvalRun.Status.PENDING,
                    run_type=run_type,
                )
                self.stdout.write(f"  Scheduling run {run.id} for scenario '{scenario.slug}'...")

                run_eval_task.delay(str(run.id))
                run_ids.append(run)
                created_for_suite += 1

            suite_runs.append(suite_run)
            if created_for_suite == 0:
                suite_run.status = EvalSuiteRun.Status.ERRORED
                suite_run.finished_at = timezone.now()
                suite_run.save(update_fields=["status", "finished_at", "updated_at"])
            _update_suite_state(suite_run.id)

        self.stdout.write(self.style.SUCCESS(f"Dispatched {len(run_ids)} scenario runs across {len(suite_runs)} suite(s)."))

        # Wait and report
        self.stdout.write("\n--- Waiting for Results ---\n")

        pending_ids = {run.id for run in run_ids}
        printed_tasks = {run.id: set() for run in run_ids}
        total_tasks_all = 0
        passed_tasks_all = 0

        try:
            while pending_ids:
                current_runs = EvalRun.objects.filter(id__in=pending_ids)

                for run in current_runs:
                    for task in run.tasks.all().order_by("sequence"):
                        task_key = f"{task.sequence}-{task.status}"

                        terminal_states = [
                            EvalRunTask.Status.PASSED,
                            EvalRunTask.Status.FAILED,
                            EvalRunTask.Status.ERRORED,
                            EvalRunTask.Status.SKIPPED,
                        ]

                        if task.status in terminal_states and task_key not in printed_tasks[run.id]:
                            status_color = self.style.SUCCESS if task.status == EvalRunTask.Status.PASSED else self.style.ERROR
                            self.stdout.write(f"[{run.scenario_slug}] Task {task.name}: " + status_color(f"{task.status}"))
                            if task.status == EvalRunTask.Status.FAILED:
                                self.stdout.write(f"    Reason: {task.observed_summary}")

                            printed_tasks[run.id].add(task_key)

                    if run.status in (EvalRun.Status.COMPLETED, EvalRun.Status.ERRORED):
                        self.stdout.write(f"Run {run.id} ({run.scenario_slug}) finished: {run.status}")
                        pending_ids.remove(run.id)
                        _update_suite_state(run.suite_run_id)

                if pending_ids:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nPolling interrupted. Runs may still be processing in background."))
            return

        # Final summary
        self.stdout.write("\n--- Final Summary ---")
        for run in run_ids:
            run.refresh_from_db()
            for task in run.tasks.all():
                total_tasks_all += 1
                if task.status == EvalRunTask.Status.PASSED:
                    passed_tasks_all += 1

        if total_tasks_all > 0:
            pass_rate = (passed_tasks_all / total_tasks_all) * 100
            color = (
                self.style.SUCCESS
                if pass_rate == 100
                else (self.style.WARNING if pass_rate > 50 else self.style.ERROR)
            )
            self.stdout.write(color(f"\nTotal Pass Rate: {pass_rate:.1f}% ({passed_tasks_all}/{total_tasks_all} tasks)"))
        else:
            self.stdout.write("No tasks executed.")

        for suite_run in suite_runs:
            suite_run.refresh_from_db()
            _update_suite_state(suite_run.id)
            self.stdout.write(
                f"Suite {suite_run.suite_slug} ({suite_run.id}) finished with status {suite_run.status}"
            )

        # Kick off GC after finishing this invocation
        try:
            gc_eval_runs_task.delay()
        except Exception:
            self.stdout.write(self.style.WARNING("Unable to enqueue eval GC task."))
