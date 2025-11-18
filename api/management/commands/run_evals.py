
from django.core.management.base import BaseCommand, CommandError
from api.models import PersistentAgent, EvalRun, BrowserUseAgent
from api.evals.registry import ScenarioRegistry
from api.evals.tasks import run_eval_task
from django.contrib.auth import get_user_model
import uuid

class Command(BaseCommand):
    help = 'Runs the evaluation suite or specific scenarios.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--scenario',
            type=str,
            help='Slug of the specific scenario to run (default: run all)',
        )
        parser.add_argument(
            '--agent-id',
            type=str,
            help='UUID of an existing agent to test against. If omitted, a temporary agent is created.',
        )
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously (eager mode) for debugging.',
        )

    def handle(self, *args, **options):
        scenario_slug = options['scenario']
        agent_id = options['agent_id']
        sync_mode = options['sync']

        if sync_mode:
            from django.conf import settings
            settings.CELERY_TASK_ALWAYS_EAGER = True
            settings.CELERY_TASK_EAGER_PROPAGATES = True
            self.stdout.write("Running in SYNCHRONOUS mode.")

        # 1. Select Scenarios
        scenarios_to_run = []
        if scenario_slug:
            scenario = ScenarioRegistry.get(scenario_slug)
            if not scenario:
                raise CommandError(f"Scenario '{scenario_slug}' not found.")
            scenarios_to_run.append(scenario)
        else:
            scenarios_to_run = list(ScenarioRegistry.list_all().values())

        if not scenarios_to_run:
            self.stdout.write(self.style.WARNING("No scenarios found to run."))
            return

        # 2. Prepare Agent
        agent = None
        User = get_user_model()
        # Ensure we have a user for attribution
        user, _ = User.objects.get_or_create(username="eval_runner", defaults={"email": "eval@localhost"})

        if agent_id:
            try:
                agent = PersistentAgent.objects.get(id=agent_id)
                self.stdout.write(f"Using existing agent: {agent.name} ({agent.id})")
            except PersistentAgent.DoesNotExist:
                raise CommandError(f"Agent {agent_id} not found.")
        else:
            # Create temp agent
            unique_id = str(uuid.uuid4())[:8]
            browser_agent = BrowserUseAgent.objects.create(name=f"Eval Browser {unique_id}", user=user)
            agent = PersistentAgent.objects.create(
                name=f"Eval Agent {unique_id}",
                user=user,
                browser_use_agent=browser_agent,
                execution_environment="eval",
                charter="You are a test agent."
            )
            self.stdout.write(f"Created temporary agent: {agent.name} ({agent.id})")

        # 3. Launch Runs
        run_ids = []
        for scenario in scenarios_to_run:
            run = EvalRun.objects.create(
                scenario_slug=scenario.slug,
                agent=agent,
                initiated_by=user,
                status=EvalRun.Status.PENDING
            )
            self.stdout.write(f"Scheduling run {run.id} for scenario '{scenario.slug}'...")
            
            # Dispatch
            run_eval_task.delay(str(run.id))
            run_ids.append(run)

        self.stdout.write(self.style.SUCCESS(f"Dispatched {len(run_ids)} eval runs."))
        
        if sync_mode:
            self.stdout.write("\n--- Results ---\n")
            total_tasks = 0
            passed_tasks = 0
            
            for run in run_ids:
                run.refresh_from_db()
                self.stdout.write(f"Run {run.id} ({run.scenario_slug}): {run.status}")
                for task in run.tasks.all():
                    total_tasks += 1
                    status_color = self.style.SUCCESS if task.status == "passed" else self.style.ERROR
                    if task.status == "passed":
                        passed_tasks += 1
                    
                    self.stdout.write(f"  - {task.name}: " + status_color(f"{task.status}"))
                    if task.status == "failed":
                        self.stdout.write(f"    Reason: {task.observed_summary}")
            
            if total_tasks > 0:
                pass_rate = (passed_tasks / total_tasks) * 100
                color = self.style.SUCCESS if pass_rate == 100 else (self.style.WARNING if pass_rate > 50 else self.style.ERROR)
                self.stdout.write(color(f"\nTotal Pass Rate: {pass_rate:.1f}% ({passed_tasks}/{total_tasks} tasks)"))
            else:
                self.stdout.write("No tasks executed.")
