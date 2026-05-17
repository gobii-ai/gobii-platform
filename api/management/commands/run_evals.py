import argparse
import time
import uuid
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

import api.evals.loader  # noqa: F401 - registers canonical scenarios and suites
from api.evals.catalog import (
    ScenarioCatalogFilters,
    filter_scenario_slugs,
    get_scenario_metadata,
    normalized_filter_values,
    scenario_to_suite_slugs,
)
from api.evals.local_setup import ensure_eval_local_setup, get_eval_local_routing_profile_seeds
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry
from api.evals.tasks import run_eval_task, gc_eval_runs_task
from api.evals.runner import _update_suite_state
from api.models import BrowserUseAgent, EvalRun, EvalRunTask, EvalSuiteRun, LLMRoutingProfile, PersistentAgent
from api.services.llm_routing_profile_snapshot import create_eval_profile_snapshot


@dataclass(frozen=True)
class EvalExecutionPlan:
    effective_max_concurrency: int
    use_eager_thread_pool: bool
    warn_sqlite_serial: bool


def build_eval_execution_plan(
    *,
    sync_mode: bool,
    celery_task_always_eager: bool,
    using_sqlite: bool,
    requested_max_concurrency: int,
    queued_run_count: int,
) -> EvalExecutionPlan:
    if sync_mode or (celery_task_always_eager and using_sqlite):
        effective_max_concurrency = 1
    elif celery_task_always_eager:
        effective_max_concurrency = requested_max_concurrency or max(1, queued_run_count)
    else:
        effective_max_concurrency = requested_max_concurrency

    return EvalExecutionPlan(
        effective_max_concurrency=effective_max_concurrency,
        use_eager_thread_pool=(
            celery_task_always_eager
            and not sync_mode
            and effective_max_concurrency != 1
        ),
        warn_sqlite_serial=(
            celery_task_always_eager
            and using_sqlite
            and not sync_mode
            and requested_max_concurrency != 1
        ),
    )


class Command(BaseCommand):
    help = (
        "Run canonical Gobii eval scenarios and suites through EvalRunner. Use "
        "--settings=config.eval_local_settings for explicit local SQLite setup, or "
        "--settings=config.eval_postgres_settings for concurrent local live suites."
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
            "  uv run python manage.py run_evals --suite all --n-runs 1 "
            "--routing-profile openrouter-deepseek-v4-flash --max-concurrency 8 "
            "--settings=config.eval_postgres_settings\n"
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
                "With eval-local settings this also creates the local schema and seeds local profiles."
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
            action="append",
            type=str,
            help=(
                "LLM routing profile name or UUID to snapshot onto the eval suite run. Can be repeated or comma-separated "
                "to run a model/profile matrix. "
                "Eval-local settings seed OpenRouter, OpenAI, and optional custom LiteLLM profiles."
            ),
        )
        parser.add_argument(
            "--tag",
            action="append",
            default=[],
            help="Only include scenarios with this metadata tag. Can be repeated or comma-separated.",
        )
        parser.add_argument(
            "--tier",
            action="append",
            default=[],
            help="Only include scenarios in this tier, such as smoke, core, extended, or manual.",
        )
        parser.add_argument(
            "--category",
            action="append",
            default=[],
            help="Only include scenarios in this category, such as planning, tool_choice, or meta_gobii.",
        )
        parser.add_argument(
            "--cost-class",
            action="append",
            default=[],
            help="Only include scenarios with this cost class, such as low, medium, or high.",
        )
        parser.add_argument(
            "--runtime-class",
            action="append",
            default=[],
            help="Only include scenarios with this expected runtime class, such as short, medium, or long.",
        )
        parser.add_argument(
            "--owner",
            action="append",
            default=[],
            help="Only include scenarios owned by this team/person.",
        )
        parser.add_argument(
            "--area",
            action="append",
            default=[],
            help="Only include scenarios for this product/agent area.",
        )
        parser.add_argument(
            "--requires-secret",
            action="append",
            default=[],
            help="Only include scenarios that declare this required secret/secret class.",
        )
        parser.add_argument(
            "--simulation-supported",
            action="store_true",
            help="Only include scenarios that declare deterministic simulation support.",
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
        parser.add_argument(
            "--max-concurrency",
            type=int,
            default=4,
            help=(
                "Maximum queued eval runs to keep in flight while polling async workers. "
                "Use 0 for unlimited. Synchronous/eager mode still executes one run at a time."
            ),
        )
        parser.add_argument(
            "--max-runs-total",
            type=int,
            default=0,
            help="Safety cap for total EvalRun rows created by this invocation. Use 0 for no cap.",
        )
        parser.add_argument(
            "--max-scenarios",
            type=int,
            default=0,
            help="Safety cap for selected unique scenario slugs after suite/filter expansion. Use 0 for no cap.",
        )

    def _build_catalog_filters(self, options) -> ScenarioCatalogFilters:
        return ScenarioCatalogFilters(
            tags=normalized_filter_values(options.get("tag")),
            tiers=normalized_filter_values(options.get("tier")),
            categories=normalized_filter_values(options.get("category")),
            cost_classes=normalized_filter_values(options.get("cost_class")),
            runtime_classes=normalized_filter_values(options.get("runtime_class")),
            owners=normalized_filter_values(options.get("owner")),
            areas=normalized_filter_values(options.get("area")),
            required_secrets=normalized_filter_values(options.get("requires_secret")),
            simulation_supported=True if options.get("simulation_supported") else None,
        )

    def _filter_summary(self, filters: ScenarioCatalogFilters) -> str:
        parts = []
        if filters.tags:
            parts.append(f"tag={','.join(filters.tags)}")
        if filters.tiers:
            parts.append(f"tier={','.join(filters.tiers)}")
        if filters.categories:
            parts.append(f"category={','.join(filters.categories)}")
        if filters.cost_classes:
            parts.append(f"cost={','.join(filters.cost_classes)}")
        if filters.runtime_classes:
            parts.append(f"runtime={','.join(filters.runtime_classes)}")
        if filters.owners:
            parts.append(f"owner={','.join(filters.owners)}")
        if filters.areas:
            parts.append(f"area={','.join(filters.areas)}")
        if filters.required_secrets:
            parts.append(f"requires_secret={','.join(filters.required_secrets)}")
        if filters.simulation_supported is True:
            parts.append("simulation_supported=true")
        return "; ".join(parts)

    def _print_eval_catalog(self, filters: ScenarioCatalogFilters | None = None) -> None:
        filters = filters or ScenarioCatalogFilters()
        suites = SuiteRegistry.list_all()
        scenarios = ScenarioRegistry.list_all()
        suite_mapping = scenario_to_suite_slugs()
        filtered_scenario_slugs = set(filter_scenario_slugs(scenarios.keys(), filters))

        self.stdout.write("Available eval suites:")
        for suite_slug, suite in sorted(suites.items(), key=lambda item: (item[0] == "all", item[0])):
            selected_count = len(filter_scenario_slugs(suite.scenario_slugs, filters))
            self.stdout.write(
                f"  {suite_slug} ({selected_count}/{len(suite.scenario_slugs)} matching scenarios) - {suite.description}"
            )

        filter_text = self._filter_summary(filters)
        if filter_text:
            self.stdout.write(f"\nScenario filters: {filter_text}")
        self.stdout.write("\nAvailable eval scenarios:")
        for scenario_slug, scenario in sorted(scenarios.items()):
            if scenario_slug not in filtered_scenario_slugs:
                continue
            suites_text = ", ".join(suite_mapping.get(scenario_slug, [])) or "ad-hoc only"
            metadata = get_scenario_metadata(scenario)
            tags_text = ",".join(metadata.tags[:6]) or "none"
            simulated_text = " sim" if metadata.supports_simulation else ""
            self.stdout.write(
                f"  {scenario_slug} [{suites_text}] "
                f"tier={metadata.tier} category={metadata.category} runtime={metadata.expected_runtime} "
                f"cost={metadata.cost_class}{simulated_text} tags={tags_text} - {scenario.description}"
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

    def _routing_profile_refs(self, raw_refs) -> list[str]:
        refs = []
        for raw_ref in raw_refs or []:
            for part in str(raw_ref).split(","):
                part = part.strip()
                if part:
                    refs.append(part)
        return list(dict.fromkeys(refs))

    def _resolve_routing_profile(self, routing_profile_ref: str) -> LLMRoutingProfile:
        routing_profile_uuid = None
        try:
            routing_profile_uuid = uuid.UUID(routing_profile_ref)
        except ValueError:
            pass

        source_routing_profile = None
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

        if source_routing_profile:
            return source_routing_profile

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
        routing_profile_refs = self._routing_profile_refs(options.get("routing_profile"))
        catalog_filters = self._build_catalog_filters(options)
        simulated = bool(options.get("simulated"))
        delay_between_runs = max(0, float(options.get("delay_between_runs_seconds") or 0))
        max_concurrency = max(0, int(options.get("max_concurrency") or 0))
        max_runs_total = max(0, int(options.get("max_runs_total") or 0))
        max_scenarios = max(0, int(options.get("max_scenarios") or 0))
        base_site_url = (settings.PUBLIC_SITE_URL or "http://localhost:8000").rstrip("/")
        printed_audit_agents: set[str] = set()

        if list_catalog:
            self._print_eval_catalog(catalog_filters)

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

        filtered_suites = []
        for suite_slug, scenario_slugs, description in suites:
            filtered_slugs = filter_scenario_slugs(scenario_slugs, catalog_filters)
            if max_scenarios and len(filtered_slugs) > max_scenarios:
                raise CommandError(
                    f"Selected {len(filtered_slugs)} scenarios after filters, which exceeds --max-scenarios={max_scenarios}."
                )
            if not filtered_slugs:
                filter_text = self._filter_summary(catalog_filters) or "none"
                self.stdout.write(
                    self.style.WARNING(
                        f"Suite {suite_slug} has no scenarios after filters ({filter_text}); skipping."
                    )
                )
                continue
            filtered_suites.append((suite_slug, filtered_slugs, description))
        suites = filtered_suites

        if simulated:
            unsupported = []
            for _suite_slug, scenario_slugs, _description in suites:
                for slug in scenario_slugs:
                    scenario = ScenarioRegistry.get(slug)
                    if not scenario or not get_scenario_metadata(scenario).supports_simulation:
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

        source_routing_profiles = [
            self._resolve_routing_profile(ref)
            for ref in routing_profile_refs
        ]
        if source_routing_profiles:
            if len(source_routing_profiles) > 1:
                self.stdout.write(
                    f"Running routing-profile matrix with {len(source_routing_profiles)} profiles."
                )
            for source_routing_profile in source_routing_profiles:
                self.stdout.write(
                    f"Using routing profile {source_routing_profile.name} ({source_routing_profile.id})"
                )
        elif not simulated:
            self.stdout.write(
                self.style.WARNING(
                    "No --routing-profile supplied; live runs will use the active database LLM routing profile. "
                    "Local eval runs usually should pass an eval-local --settings module and an explicit "
                    "--routing-profile."
                )
            )
        routing_profile_matrix = source_routing_profiles or [None]
        selected_run_count = sum(len(scenario_slugs) for _suite_slug, scenario_slugs, _description in suites)
        total_run_count = selected_run_count * requested_runs * len(routing_profile_matrix)
        if max_runs_total and total_run_count > max_runs_total:
            raise CommandError(
                f"This invocation would create {total_run_count} EvalRun rows, exceeding --max-runs-total={max_runs_total}."
            )

        filter_text = self._filter_summary(catalog_filters)
        if filter_text:
            self.stdout.write(f"Applying scenario filters: {filter_text}")
        self.stdout.write(
            f"Selected {selected_run_count} scenario(s), {requested_runs} repeat(s), "
            f"{len(routing_profile_matrix)} routing profile slot(s): {total_run_count} total run(s)."
        )

        # Base user attribution
        User = get_user_model()
        user, _ = User.objects.get_or_create(username="eval_runner", defaults={"email": "eval@localhost"})
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

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

        for source_routing_profile in routing_profile_matrix:
            profile_name = source_routing_profile.name if source_routing_profile else "active-default"
            for suite_slug, scenario_slugs, description in suites:
                scenario_slugs = list(dict.fromkeys(scenario_slugs))
                suite_run_id = uuid.uuid4()
                profile_snapshot = None
                if source_routing_profile:
                    profile_snapshot = create_eval_profile_snapshot(source_routing_profile, str(suite_run_id))
                launch_config = {}
                if simulated:
                    launch_config["mode"] = "simulated"
                if filter_text:
                    launch_config["scenario_filters"] = filter_text
                if len(routing_profile_matrix) > 1:
                    launch_config["matrix_profile"] = profile_name
                suite_run = EvalSuiteRun.objects.create(
                    id=suite_run_id,
                    suite_slug=suite_slug,
                    launch_config=launch_config,
                    initiated_by=user,
                    status=EvalSuiteRun.Status.RUNNING,
                    run_type=run_type,
                    requested_runs=requested_runs,
                    agent_strategy=agent_strategy,
                    shared_agent=shared_agent if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT else None,
                    started_at=timezone.now(),
                    llm_routing_profile=profile_snapshot,
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created suite run {suite_run.id} ({suite_slug}) [{run_type}] profile={profile_name}"
                    )
                )

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
                        self.stdout.write(
                            f"  Queued run {run.id} for scenario '{scenario.slug}' "
                            f"(iteration {iteration + 1}/{requested_runs}, profile={profile_name})."
                        )
                        run_ids.append(run)
                        created_for_suite += 1

                suite_runs.append(suite_run)
                if created_for_suite == 0:
                    suite_run.status = EvalSuiteRun.Status.ERRORED
                    suite_run.finished_at = timezone.now()
                    suite_run.save(update_fields=["status", "finished_at", "updated_at"])
                _update_suite_state(suite_run.id)

        self.stdout.write(self.style.SUCCESS(f"Prepared {len(run_ids)} scenario runs across {len(suite_runs)} suite(s)."))

        # Wait and report
        self.stdout.write("\n--- Waiting for Results ---\n")

        run_queue = list(run_ids)
        active_ids = set()
        active_futures = {}
        printed_tasks = {run.id: set() for run in run_ids}
        total_tasks_all = 0
        passed_tasks_all = 0
        terminal_task_states = [
            EvalRunTask.Status.PASSED,
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        ]
        execution_plan = build_eval_execution_plan(
            sync_mode=sync_mode,
            celery_task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
            using_sqlite=settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3",
            requested_max_concurrency=max_concurrency,
            queued_run_count=len(run_queue),
        )
        if execution_plan.effective_max_concurrency:
            self.stdout.write(f"Max in-flight eval runs: {execution_plan.effective_max_concurrency}")
        else:
            self.stdout.write("Max in-flight eval runs: unlimited")
        if execution_plan.warn_sqlite_serial:
            self.stdout.write(
                self.style.WARNING(
                    "Local SQLite evals run serially to avoid database write locks. "
                    "Use config.eval_postgres_settings or async workers for concurrent live suites."
                )
            )
        if execution_plan.use_eager_thread_pool:
            self.stdout.write("Using local eager worker threads for eval concurrency.")

        def _run_eval_eager(run_id):
            close_old_connections()
            try:
                run_eval_task.apply(args=(str(run_id),), throw=True)
            finally:
                close_old_connections()

        executor = (
            ThreadPoolExecutor(max_workers=execution_plan.effective_max_concurrency)
            if execution_plan.use_eager_thread_pool
            else None
        )
        try:
            while run_queue or active_ids:
                while run_queue and (
                    not execution_plan.effective_max_concurrency
                    or len(active_ids) < execution_plan.effective_max_concurrency
                ):
                    run = run_queue.pop(0)
                    self.stdout.write(f"Scheduling run {run.id} for scenario '{run.scenario_slug}'...")
                    if executor:
                        active_futures[run.id] = executor.submit(_run_eval_eager, run.id)
                    else:
                        run_eval_task.delay(str(run.id))
                    active_ids.add(run.id)
                    if delay_between_runs:
                        time.sleep(delay_between_runs)

                current_runs = EvalRun.objects.filter(id__in=active_ids).prefetch_related("tasks")
                finished_ids = set()
                for run_id, future in list(active_futures.items()):
                    if not future.done():
                        continue
                    try:
                        future.result()
                    except Exception as exc:
                        run = EvalRun.objects.filter(id=run_id).first()
                        if run and run.status in (EvalRun.Status.PENDING, EvalRun.Status.RUNNING):
                            run.status = EvalRun.Status.ERRORED
                            run.notes = f"Local eager worker failed: {type(exc).__name__}: {exc}"
                            run.finished_at = timezone.now()
                            run.save(update_fields=["status", "notes", "finished_at", "updated_at"])
                            _update_suite_state(run.suite_run_id)
                        self.stdout.write(self.style.ERROR(f"Run {run_id} failed in local eager worker: {exc}"))
                    finally:
                        del active_futures[run_id]

                for run in current_runs:
                    for task in run.tasks.all().order_by("sequence"):
                        task_key = f"{task.sequence}-{task.status}"

                        if task.status in terminal_task_states and task_key not in printed_tasks[run.id]:
                            status_color = self.style.SUCCESS if task.status == EvalRunTask.Status.PASSED else self.style.ERROR
                            self.stdout.write(f"[{run.scenario_slug}] Task {task.name}: " + status_color(f"{task.status}"))
                            if task.status == EvalRunTask.Status.FAILED:
                                self.stdout.write(f"    Reason: {task.observed_summary}")

                            printed_tasks[run.id].add(task_key)

                    if run.status in (EvalRun.Status.COMPLETED, EvalRun.Status.ERRORED):
                        self.stdout.write(f"Run {run.id} ({run.scenario_slug}) finished: {run.status}")
                        finished_ids.add(run.id)
                        _update_suite_state(run.suite_run_id)

                active_ids.difference_update(finished_ids)
                if run_queue or active_ids:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nPolling interrupted. Runs may still be processing in background."))
            return
        finally:
            if executor:
                executor.shutdown(wait=True)

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
