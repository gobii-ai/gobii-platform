import argparse
import time
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers canonical scenarios and suites
from api.evals.local_setup import ensure_eval_local_setup, get_eval_local_routing_profile_seeds
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.evals.tasks import run_eval_task, gc_eval_runs_task
from api.evals.runner import _update_suite_state
from api.models import BrowserUseAgent, EvalRun, EvalRunTask, EvalSuiteRun, LLMRoutingProfile, PersistentAgent
from api.services.llm_routing_profile_snapshot import create_eval_profile_snapshot


class Command(BaseCommand):
    help = (
        "Run canonical Gobii eval scenarios and suites through EvalRunner. Use "
        "--settings=config.eval_local_settings for explicit local SQLite setup."
    )

    def add_arguments(self, parser):
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.epilog = (
            "Examples:\n"
            "  uv run python manage.py run_evals --list\n"
            "  uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 "
            "--simulated --settings=config.eval_local_settings\n"
            "  uv run python manage.py run_evals --scenario meta_gobii_negative_content_task --sync "
            "--n-runs 1 --simulated --settings=config.eval_local_settings\n"
            "  set -a; source /Users/andrew/.env-openrouter >/dev/null; set +a\n"
            "  uv run python manage.py run_evals --suite meta_gobii --sync --n-runs 1 "
            "--routing-profile openrouter-deepseek-v4-flash --settings=config.eval_local_settings\n"
            "Simulated runs are deterministic local checks and are not live model evals."
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List registered canonical eval suites and scenarios, then exit.",
        )
        parser.add_argument(
            "--list-routing-profiles",
            action="store_true",
            help=(
                "List non-snapshot LLM routing profiles available in the database, then exit. "
                "With config.eval_local_settings this also creates the local SQLite schema and seeds local profiles."
            ),
        )
        parser.add_argument(
            "--suite",
            action="append",
            dest="suites",
            help="Suite slug to run (can be repeated). Defaults to 'all'.",
        )
        parser.add_argument(
            "--scenario",
            type=str,
            help="Run one scenario by slug as a one-off suite.",
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
        parser.add_argument(
            "--n-runs",
            type=int,
            default=3,
            help="How many times to repeat each scenario (default: 3).",
        )
        parser.add_argument(
            "--routing-profile",
            "--llm-routing-profile",
            dest="routing_profile",
            type=str,
            help=(
                "LLM routing profile name or UUID to snapshot onto the eval suite run. "
                "config.eval_local_settings seeds OpenRouter, OpenAI, and optional custom LiteLLM profiles."
            ),
        )
        parser.add_argument(
            "--simulated",
            action="store_true",
            help=(
                "Run scenario-provided deterministic simulations through the canonical runner. "
                "This is not a live model eval."
            ),
        )
        parser.add_argument(
            "--delay-between-runs-seconds",
            type=float,
            default=0,
            help="Optional sleep between scenario runs/submissions for local live evals against rate-limited providers.",
        )

    def _print_eval_catalog(self) -> None:
        suites = SuiteRegistry.list_all()
        scenarios = ScenarioRegistry.list_all()
        scenario_to_suites: dict[str, list[str]] = {}
        for suite_slug, suite in suites.items():
            if suite_slug == "all":
                continue
            for scenario_slug in suite.scenario_slugs:
                scenario_to_suites.setdefault(scenario_slug, []).append(suite_slug)

        self.stdout.write("Available eval suites:")
        for suite_slug, suite in sorted(suites.items(), key=lambda item: (item[0] == "all", item[0])):
            self.stdout.write(
                f"  {suite_slug} ({len(suite.scenario_slugs)} scenarios) - {suite.description}"
            )

        self.stdout.write("\nAvailable eval scenarios:")
        for scenario_slug, scenario in sorted(scenarios.items()):
            suites_text = ", ".join(sorted(scenario_to_suites.get(scenario_slug, []))) or "ad-hoc only"
            simulated_text = " simulated" if getattr(scenario, "supports_simulation", False) else ""
            self.stdout.write(
                f"  {scenario_slug} [{suites_text}{simulated_text}] - {scenario.description}"
            )

        local_profiles = get_eval_local_routing_profile_seeds()
        if local_profiles:
            self.stdout.write("\nLocal eval settings can seed these routing profiles:")
            for seed in local_profiles:
                self.stdout.write(
                    f"  {seed.profile_name} -> {seed.litellm_model} via {seed.provider_env_var_name}"
                )

        self.stdout.write("\nRun `uv run python manage.py run_evals --help` for command examples.")

    def _print_routing_profiles(self) -> None:
        profiles = (
            LLMRoutingProfile.objects.filter(is_eval_snapshot=False)
            .prefetch_related(
                "persistent_token_ranges__tiers__tier_endpoints__endpoint__provider"
            )
            .order_by("name")
        )
        if not profiles:
            self.stdout.write("No non-snapshot LLM routing profiles found.")
            return

        self.stdout.write("Available LLM routing profiles:")
        for profile in profiles:
            models = []
            for token_range in profile.persistent_token_ranges.all():
                for tier in token_range.tiers.all():
                    for tier_endpoint in tier.tier_endpoints.all():
                        endpoint = tier_endpoint.endpoint
                        label = f"{endpoint.provider.key}:{endpoint.litellm_model}"
                        if label not in models:
                            models.append(label)
            model_text = ", ".join(models) if models else "no persistent endpoints"
            self.stdout.write(f"  {profile.name} - {profile.display_name} ({model_text})")

    def handle(self, *args, **options):
        list_catalog = bool(options["list"])
        list_routing_profiles = bool(options["list_routing_profiles"])
        suites_requested = options["suites"] or []
        scenario_slug = options["scenario"]
        agent_id = options["agent_id"]
        agent_strategy = options["agent_strategy"]
        sync_mode = options["sync"]
        run_type_option = options["run_type"]
        run_type = EvalSuiteRun.RunType.OFFICIAL if options["official"] else run_type_option
        requested_runs = max(1, min(10, int(options.get("n_runs") or 1)))
        routing_profile_ref = (options.get("routing_profile") or "").strip()
        simulated = bool(options.get("simulated"))
        delay_between_runs = max(0, float(options.get("delay_between_runs_seconds") or 0))
        base_site_url = (settings.PUBLIC_SITE_URL or "http://localhost:8000").rstrip("/")
        printed_audit_agents: set[str] = set()

        if list_catalog:
            self._print_eval_catalog()

        if list_routing_profiles and settings.EVAL_LOCAL_SETUP_ENABLED:
            ensure_eval_local_setup(stdout=self.stdout)

        if list_routing_profiles:
            if list_catalog:
                self.stdout.write("")
            self._print_routing_profiles()

        if list_catalog or list_routing_profiles:
            return

        if sync_mode:
            settings.CELERY_TASK_ALWAYS_EAGER = True
            settings.CELERY_TASK_EAGER_PROPAGATES = True
            self.stdout.write("Running in SYNCHRONOUS mode.")

        if settings.EVAL_LOCAL_SETUP_ENABLED:
            ensure_eval_local_setup(stdout=self.stdout)

        # Resolve suites
        suite_slugs: list[str] = suites_requested[:]
        if scenario_slug:
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

        if simulated:
            unsupported = []
            for _suite_slug, scenario_slugs, _description in suites:
                for slug in scenario_slugs:
                    scenario = ScenarioRegistry.get(slug)
                    if not scenario or not getattr(scenario, "supports_simulation", False):
                        unsupported.append(slug)
            if unsupported:
                raise CommandError(
                    "--simulated is only available for scenarios that declare simulation support. "
                    f"Unsupported scenario(s): {', '.join(sorted(unsupported))}"
                )

        if simulated:
            self.stdout.write(self.style.WARNING("Running in SIMULATED mode. No live model calls will be made."))

        if not suites:
            self.stdout.write(self.style.WARNING("No suites found to run."))
            return

        source_routing_profile = None
        if routing_profile_ref:
            routing_profile_uuid = None
            try:
                routing_profile_uuid = uuid.UUID(routing_profile_ref)
            except ValueError:
                pass

            if routing_profile_uuid:
                source_routing_profile = LLMRoutingProfile.objects.filter(
                    id=routing_profile_uuid,
                    is_eval_snapshot=False,
                ).first()

            if not source_routing_profile:
                source_routing_profile = LLMRoutingProfile.objects.filter(
                    name=routing_profile_ref,
                    is_eval_snapshot=False,
                ).first()

            if not source_routing_profile:
                snapshot_match = False
                if routing_profile_uuid:
                    snapshot_match = LLMRoutingProfile.objects.filter(
                        id=routing_profile_uuid,
                        is_eval_snapshot=True,
                    ).exists()
                if not snapshot_match:
                    snapshot_match = LLMRoutingProfile.objects.filter(
                        name=routing_profile_ref,
                        is_eval_snapshot=True,
                    ).exists()
                if snapshot_match:
                    raise CommandError("Routing profile must reference a non-snapshot profile.")
                raise CommandError(f"Routing profile '{routing_profile_ref}' not found.")

            self.stdout.write(
                f"Using routing profile {source_routing_profile.name} ({source_routing_profile.id})"
            )
        elif not simulated:
            self.stdout.write(
                self.style.WARNING(
                    "No --routing-profile supplied; live runs will use the active database LLM routing profile. "
                    "Local eval runs usually should pass --settings=config.eval_local_settings and an explicit "
                    "--routing-profile."
                )
            )

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

        def _print_audit_link(agent: PersistentAgent) -> None:
            agent_id = str(agent.id)
            if agent_id in printed_audit_agents:
                return
            self.stdout.write(f"  Audit agent timeline: {base_site_url}/console/staff/agents/{agent_id}/audit/")
            printed_audit_agents.add(agent_id)

        shared_agent: PersistentAgent | None = None
        if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT:
            if not agent_id:
                raise CommandError("--agent-id is required when agent-strategy is reuse_agent")
            try:
                shared_agent = PersistentAgent.objects.get(id=agent_id)
            except PersistentAgent.DoesNotExist:
                raise CommandError(f"Agent {agent_id} not found.")
            self.stdout.write(f"Using provided agent for reuse: {shared_agent.name} ({shared_agent.id})")
            _print_audit_link(shared_agent)

        suite_runs = []
        run_ids = []

        for suite_slug, scenario_slugs, description in suites:
            scenario_slugs = list(dict.fromkeys(scenario_slugs))
            suite_run_id = uuid.uuid4()
            profile_snapshot = None
            if source_routing_profile:
                profile_snapshot = create_eval_profile_snapshot(source_routing_profile, str(suite_run_id))
            suite_run = EvalSuiteRun.objects.create(
                id=suite_run_id,
                suite_slug=suite_slug,
                launch_config={"mode": "simulated"} if simulated else {},
                initiated_by=user,
                status=EvalSuiteRun.Status.RUNNING,
                run_type=run_type,
                requested_runs=requested_runs,
                agent_strategy=agent_strategy,
                shared_agent=shared_agent if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT else None,
                started_at=timezone.now(),
                llm_routing_profile=profile_snapshot,
            )

            self.stdout.write(self.style.SUCCESS(f"Created suite run {suite_run.id} ({suite_slug}) [{run_type}]"))

            created_for_suite = 0
            for scenario_slug in scenario_slugs:
                scenario = ScenarioRegistry.get(scenario_slug)
                if not scenario:
                    self.stdout.write(self.style.ERROR(f"Scenario '{scenario_slug}' missing; skipping."))
                    continue

                for iteration in range(requested_runs):
                    run_agent = shared_agent
                    if agent_strategy == EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO or run_agent is None:
                        run_agent = _create_ephemeral_agent(label_suffix=f"{scenario.slug[:8]}-{iteration + 1}")
                        self.stdout.write(f"  Created ephemeral agent for {scenario.slug}: {run_agent.id}")
                        _print_audit_link(run_agent)

                    run = EvalRun.objects.create(
                        suite_run=suite_run,
                        scenario_slug=scenario.slug,
                        scenario_version=getattr(scenario, "version", "") or "",
                        agent=run_agent,
                        initiated_by=user,
                        status=EvalRun.Status.PENDING,
                        run_type=run_type,
                    )
                    self.stdout.write(f"  Scheduling run {run.id} for scenario '{scenario.slug}' (iteration {iteration + 1}/{requested_runs})...")

                    run_eval_task.delay(str(run.id))
                    run_ids.append(run)
                    created_for_suite += 1
                    if delay_between_runs:
                        time.sleep(delay_between_runs)

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
