from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.utils import timezone

from api.agent.tasks.process_events import process_agent_schedule_trigger_task
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCronTrigger,
    PersistentAgentSchedule,
    UserBilling,
)
from api.services.agent_schedules import (
    claim_schedule_occurrence,
    compute_next_run,
    create_default_onboarding_schedule,
    reconcile_agent_schedules,
    sync_schedule_entry,
)
from api.services.persistent_agents import PersistentAgentProvisioningService


@tag("batch_schedule")
class ScheduleTimingTests(SimpleTestCase):
    def test_one_time_schedule_preserves_exact_second(self):
        run_at = datetime(2026, 7, 22, 18, 30, 17, tzinfo=datetime_timezone.utc)

        result = compute_next_run(
            {"kind": "once", "run_at": run_at},
            after=run_at - timedelta(seconds=1),
        )

        self.assertEqual(result, run_at)

    def test_one_time_schedule_normalizes_subsecond_input(self):
        run_at = datetime(2026, 7, 22, 18, 30, 17, 654321, tzinfo=datetime_timezone.utc)

        result = compute_next_run(
            {"kind": "once", "run_at": run_at},
            after=run_at - timedelta(seconds=1),
        )

        self.assertEqual(
            result,
            datetime(2026, 7, 22, 18, 30, 17, tzinfo=datetime_timezone.utc),
        )

    def test_daily_cron_stays_at_local_hour_across_dst(self):
        result = compute_next_run(
            {
                "kind": "recurring",
                "expression": "0 9 * * *",
                "timezone": "America/New_York",
            },
            after=datetime(2026, 3, 7, 14, 1, tzinfo=datetime_timezone.utc),
        )

        self.assertEqual(result, datetime(2026, 3, 8, 13, 0, tzinfo=datetime_timezone.utc))

    def test_nonexistent_dst_wall_time_is_skipped(self):
        result = compute_next_run(
            {
                "kind": "recurring",
                "expression": "30 2 * * *",
                "timezone": "America/New_York",
            },
            after=datetime(2026, 3, 8, 5, 0, tzinfo=datetime_timezone.utc),
        )

        self.assertEqual(result, datetime(2026, 3, 9, 6, 30, tzinfo=datetime_timezone.utc))


@tag("batch_schedule")
@override_settings(
    PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE=12,
    PERSISTENT_AGENT_SCHEDULE_MAX_TOTAL=40,
    PERSISTENT_AGENT_SCHEDULE_MAX_RECURRING_RUNS_PER_DAY=96,
    PERSISTENT_AGENT_SCHEDULE_MIN_ONCE_LEAD_SECONDS=5,
)
class AgentScheduleServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="multi-schedule-owner",
            email="multi-schedule@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Scheduler browser")
        with patch("api.models.PersistentAgent._sync_celery_beat_task"):
            self.agent = PersistentAgent.objects.create(
                user=self.user,
                name="Scheduler",
                charter="Manage several real-world cadences.",
                browser_use_agent=self.browser_agent,
            )
        self.now = datetime(2026, 7, 22, 18, 0, tzinfo=datetime_timezone.utc)
        self.tool_settings = patch(
            "api.services.agent_schedules.get_tool_settings_for_owner",
            return_value=SimpleNamespace(min_cron_schedule_minutes=30),
        )
        self.tool_settings.start()
        self.addCleanup(self.tool_settings.stop)

    def test_reconcile_creates_recurring_and_exact_timer_together(self):
        rows = [
            {
                "schedule_key": "weekday_pipeline",
                "name": "Weekday pipeline",
                "instruction": "Review qualified leads and prepare the next batch.",
                "kind": "recurring",
                "expression": "15 9 * * 1-5",
                "timezone": "America/New_York",
                "run_at": None,
                "enabled": 1,
            },
            {
                "schedule_key": "launch_timer",
                "name": "Launch timer",
                "instruction": "Check the launch status at the announced time.",
                "kind": "once",
                "expression": None,
                "timezone": "UTC",
                "run_at": "2026-07-22T18:03:17Z",
                "enabled": 1,
            },
        ]

        with patch("api.services.agent_schedules.sync_schedule_entry") as sync_mock:
            with self.captureOnCommitCallbacks(execute=True):
                result = reconcile_agent_schedules(self.agent, rows, now=self.now)

        timer = self.agent.additional_schedules.get(schedule_key="launch_timer")
        self.assertEqual(result["created"], 2)
        self.assertEqual(timer.next_run_at, datetime(2026, 7, 22, 18, 3, 17, tzinfo=datetime_timezone.utc))
        self.assertEqual(sync_mock.call_count, 2)

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MAX_ACTIVE=2)
    def test_active_cap_rolls_back_entire_reconciliation(self):
        rows = [
            {
                "schedule_key": f"timer_{index}",
                "name": f"Timer {index}",
                "instruction": "Follow up.",
                "kind": "once",
                "expression": None,
                "timezone": "UTC",
                "run_at": self.now + timedelta(hours=index + 1),
                "enabled": 1,
            }
            for index in range(3)
        ]

        with self.assertRaisesMessage(ValidationError, "at most 2 active schedules"):
            reconcile_agent_schedules(self.agent, rows, now=self.now)

        self.assertFalse(self.agent.additional_schedules.exists())

    @override_settings(PERSISTENT_AGENT_SCHEDULE_MAX_RECURRING_RUNS_PER_DAY=2)
    def test_aggregate_recurring_limit_rejects_individually_valid_schedules(self):
        rows = [
            {
                "schedule_key": f"daily_{index}",
                "name": f"Daily {index}",
                "instruction": "Review one workstream.",
                "kind": "recurring",
                "expression": f"{index} 9 * * *",
                "timezone": "UTC",
                "run_at": None,
                "enabled": 1,
            }
            for index in range(3)
        ]

        with self.assertRaisesMessage(ValidationError, "Combined recurring schedules"):
            reconcile_agent_schedules(self.agent, rows, now=self.now)

    def test_targeted_update_increments_revision_and_delete_removes_only_omitted_row(self):
        first = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="morning",
            name="Morning",
            instruction="Review status.",
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression="0 9 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=self.now + timedelta(hours=15),
        )
        removed = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="obsolete",
            name="Obsolete",
            kind=PersistentAgentSchedule.Kind.ONCE,
            run_at=self.now + timedelta(days=1),
            timezone="UTC",
            enabled=True,
            next_run_at=self.now + timedelta(days=1),
        )
        rows = [
            {
                "schedule_key": "morning",
                "name": "Morning",
                "instruction": "Review status and blockers.",
                "kind": "recurring",
                "expression": "0 9 * * *",
                "timezone": "UTC",
                "run_at": None,
                "enabled": 1,
            }
        ]

        with patch("api.services.agent_schedules.remove_agent_schedule_entries") as remove_mock:
            with patch("api.services.agent_schedules.sync_schedule_entry"):
                with self.captureOnCommitCallbacks(execute=True):
                    result = reconcile_agent_schedules(self.agent, rows, now=self.now)

        first.refresh_from_db()
        self.assertEqual(first.revision, 2)
        self.assertEqual(first.instruction, "Review status and blockers.")
        self.assertFalse(PersistentAgentSchedule.objects.filter(pk=removed.pk).exists())
        self.assertEqual(result["deleted"], 1)
        remove_mock.assert_called_once_with(self.agent.id, [removed.id])

    def test_once_claim_is_idempotent_and_disables_timer(self):
        run_at = self.now + timedelta(minutes=2, seconds=17)
        schedule = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="deadline",
            name="Deadline",
            instruction="Check the event immediately.",
            kind=PersistentAgentSchedule.Kind.ONCE,
            run_at=run_at,
            timezone="UTC",
            enabled=True,
            next_run_at=run_at,
        )

        with patch("api.services.agent_schedules.remove_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                claimed = claim_schedule_occurrence(
                    self.agent.id,
                    schedule.id,
                    1,
                    run_at.isoformat(),
                    claimed_at=run_at + timedelta(seconds=1),
                )
        duplicate = claim_schedule_occurrence(
            self.agent.id,
            schedule.id,
            1,
            run_at,
            claimed_at=run_at + timedelta(seconds=2),
        )

        schedule.refresh_from_db()
        self.assertIsNotNone(claimed)
        self.assertEqual(len(claimed.occurrence_key), 64)
        self.assertIsNone(duplicate)
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.next_run_at)
        self.assertEqual(schedule.revision, 2)

    def test_recurring_claim_rearms_at_same_wall_clock_time_after_dst(self):
        scheduled_for = datetime(2026, 3, 7, 14, 0, tzinfo=datetime_timezone.utc)
        schedule = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="local_morning",
            name="Local morning",
            instruction="Review the overnight changes.",
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression="0 9 * * *",
            timezone="America/New_York",
            enabled=True,
            next_run_at=scheduled_for,
        )

        with patch("api.services.agent_schedules.sync_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                claimed = claim_schedule_occurrence(
                    self.agent.id,
                    schedule.id,
                    1,
                    scheduled_for,
                    claimed_at=scheduled_for + timedelta(seconds=1),
                )

        schedule.refresh_from_db()
        self.assertIsNotNone(claimed)
        self.assertEqual(
            schedule.next_run_at,
            datetime(2026, 3, 8, 13, 0, tzinfo=datetime_timezone.utc),
        )
        self.assertEqual(schedule.revision, 2)

    def test_rejects_timer_without_safe_lead_time(self):
        rows = [
            {
                "schedule_key": "too_close",
                "name": "Too close",
                "instruction": "Run almost immediately.",
                "kind": "once",
                "expression": None,
                "timezone": "UTC",
                "run_at": self.now + timedelta(seconds=4),
                "enabled": 1,
            }
        ]

        with self.assertRaisesMessage(ValidationError, "at least 5 seconds"):
            reconcile_agent_schedules(self.agent, rows, now=self.now)

    def test_rejects_recurrence_below_plan_minimum(self):
        rows = [
            {
                "schedule_key": "too_fast",
                "name": "Too fast",
                "instruction": "Poll continuously.",
                "kind": "recurring",
                "expression": "@every 10m",
                "timezone": "UTC",
                "run_at": None,
                "enabled": 1,
            }
        ]

        with self.assertRaisesMessage(ValidationError, "no more often than every 30 minutes"):
            reconcile_agent_schedules(self.agent, rows, now=self.now)

    def test_primary_key_is_reserved_for_legacy_cadence(self):
        rows = [
            {
                "schedule_key": "primary",
                "name": "Conflicting primary",
                "instruction": "Replace the legacy cadence.",
                "kind": "recurring",
                "expression": "0 9 * * *",
                "timezone": "UTC",
                "run_at": None,
                "enabled": 1,
            }
        ]

        with self.assertRaisesMessage(ValidationError, "reserved for the legacy cadence"):
            reconcile_agent_schedules(self.agent, rows, now=self.now)

    @override_settings(PERSISTENT_AGENT_DEFAULT_CHECKIN_DELAY_SECONDS=86400)
    def test_default_checkin_is_one_time_and_idempotent(self):
        with patch("api.services.agent_schedules.sync_schedule_entry") as sync_mock:
            with self.captureOnCommitCallbacks(execute=True):
                first = create_default_onboarding_schedule(self.agent, now=self.now)
                second = create_default_onboarding_schedule(self.agent, now=self.now)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.kind, PersistentAgentSchedule.Kind.ONCE)
        self.assertEqual(first.run_at, self.now + timedelta(days=1))
        self.assertEqual(
            self.agent.additional_schedules.filter(schedule_key="onboarding_checkin").count(),
            1,
        )
        sync_mock.assert_called_once_with(first.id)

    @override_settings(PERSISTENT_AGENT_DEFAULT_CHECKIN_DELAY_SECONDS=86400)
    def test_product_provisioning_adds_default_checkin(self):
        before = timezone.now()

        result = PersistentAgentProvisioningService.provision(
            user=self.user,
            name="Provisioned scheduler",
            charter="Help the owner with operations.",
        )

        checkin = result.agent.additional_schedules.get(schedule_key="onboarding_checkin")
        self.assertEqual(checkin.kind, PersistentAgentSchedule.Kind.ONCE)
        self.assertTrue(checkin.enabled)
        self.assertGreaterEqual(checkin.run_at, before + timedelta(hours=23, minutes=59))
        self.assertLessEqual(checkin.run_at, timezone.now() + timedelta(days=1))

    def test_redbeat_entry_is_a_single_exact_occurrence(self):
        run_at = self.now + timedelta(minutes=2, seconds=17)
        schedule = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="exact_event",
            name="Exact event",
            kind=PersistentAgentSchedule.Kind.ONCE,
            run_at=run_at,
            timezone="UTC",
            enabled=True,
            next_run_at=run_at,
        )

        with patch("api.services.agent_schedules.RedBeatSchedulerEntry") as entry_class:
            sync_schedule_entry(schedule)

        kwargs = entry_class.call_args.kwargs
        self.assertEqual(kwargs["schedule"].dtstart, run_at)
        self.assertEqual(kwargs["schedule"].count, 1)
        self.assertEqual(kwargs["args"][3], run_at.isoformat())
        entry_class.return_value.save.assert_called_once_with()
        entry_class.return_value.reschedule.assert_called_once()

    def test_redbeat_rearm_refreshes_metadata_under_the_stable_entry_key(self):
        first_run = self.now + timedelta(minutes=2)
        schedule = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="stable_cadence",
            name="Stable cadence",
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression="0 9 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=first_run,
        )

        with patch("api.services.agent_schedules.RedBeatSchedulerEntry") as entry_class:
            sync_schedule_entry(schedule)
            schedule.next_run_at = first_run + timedelta(days=1)
            schedule.revision = 2
            sync_schedule_entry(schedule)

        first_call, second_call = entry_class.call_args_list
        self.assertEqual(first_call.kwargs["name"], second_call.kwargs["name"])
        self.assertEqual(first_call.kwargs["args"][2], 1)
        self.assertEqual(second_call.kwargs["args"][2], 2)
        self.assertEqual(entry_class.return_value.save.call_count, 2)
        self.assertEqual(entry_class.return_value.reschedule.call_count, 2)

    def test_soft_expiration_removes_and_reactivation_restores_schedule_entry(self):
        schedule = PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="durable_cadence",
            name="Durable cadence",
            kind=PersistentAgentSchedule.Kind.RECURRING,
            expression="0 9 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=self.now + timedelta(hours=15),
        )

        with patch("api.services.agent_schedules.remove_schedule_entry") as remove_mock:
            with patch("api.services.agent_schedules.RedBeatSchedulerEntry") as entry_class:
                self.agent.life_state = PersistentAgent.LifeState.EXPIRED
                with self.captureOnCommitCallbacks(execute=True):
                    self.agent.save(update_fields=["life_state"])

                remove_mock.assert_called_once_with(self.agent.id, schedule.id)
                entry_class.assert_not_called()

                self.agent.life_state = PersistentAgent.LifeState.ACTIVE
                with self.captureOnCommitCallbacks(execute=True):
                    self.agent.save(update_fields=["life_state"])

        entry_class.assert_called_once()
        entry_class.return_value.save.assert_called_once_with()
        entry_class.return_value.reschedule.assert_called_once()


@tag("batch_schedule")
class AgentScheduleTriggerTaskTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="schedule-task-owner",
            email="schedule-task@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Task browser")
        with patch("api.models.PersistentAgent._sync_celery_beat_task"):
            self.agent = PersistentAgent.objects.create(
                user=self.user,
                name="Task scheduler",
                charter="Process scheduled operations.",
                browser_use_agent=browser_agent,
            )
        self.scheduled_for = datetime(2026, 7, 22, 18, 30, tzinfo=datetime_timezone.utc)

    def _create_schedule(self, *, kind="recurring"):
        recurring = kind == "recurring"
        return PersistentAgentSchedule.objects.create(
            agent=self.agent,
            schedule_key="operations_review",
            name="Operations review",
            instruction="Review the open incidents and report material changes.",
            kind=kind,
            expression="0 14 * * *" if recurring else None,
            timezone="America/New_York" if recurring else "UTC",
            run_at=None if recurring else self.scheduled_for,
            enabled=True,
            next_run_at=self.scheduled_for,
        )

    @patch("api.agent.tasks.process_events.switch_is_active", return_value=False)
    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_recurring_task_snapshots_purpose_and_rearms(self, process_mock, _switch_mock):
        schedule = self._create_schedule()

        with patch("api.services.agent_schedules.sync_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    schedule.revision,
                    self.scheduled_for.isoformat(),
                )

        trigger = PersistentAgentCronTrigger.objects.get()
        schedule.refresh_from_db()
        self.assertEqual(trigger.schedule_id, schedule.id)
        self.assertEqual(trigger.schedule_key, "operations_review")
        self.assertEqual(trigger.schedule_name, "Operations review")
        self.assertEqual(
            trigger.schedule_instruction,
            "Review the open incidents and report material changes.",
        )
        self.assertEqual(trigger.scheduled_for, self.scheduled_for)
        self.assertEqual(len(trigger.occurrence_key), 64)
        self.assertEqual(schedule.revision, 2)
        self.assertGreater(schedule.next_run_at, self.scheduled_for)
        process_mock.assert_called_once_with(str(self.agent.id))

    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_duplicate_delivery_keeps_one_event_and_resumes_processing(self, process_mock):
        schedule = self._create_schedule(kind="once")

        with patch("api.services.agent_schedules.remove_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )

        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertEqual(PersistentAgentCronTrigger.objects.count(), 1)
        self.assertEqual(process_mock.call_count, 2)
        process_mock.assert_called_with(str(self.agent.id))

    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_owner_pause_consumes_occurrence_without_running_agent(self, process_mock):
        schedule = self._create_schedule()
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={
                "execution_paused": True,
                "execution_pause_reason": "billing_delinquency",
                "execution_paused_at": timezone.now(),
            },
        )

        with patch("api.services.agent_schedules.sync_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )

        schedule.refresh_from_db()
        self.assertEqual(schedule.revision, 2)
        self.assertGreater(schedule.next_run_at, self.scheduled_for)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        process_mock.assert_not_called()

    @patch("api.agent.tasks.process_events._scheduled_execution_is_throttled", return_value=True)
    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_free_plan_throttle_still_rearms_recurring_schedule(
        self,
        process_mock,
        _throttle_mock,
    ):
        schedule = self._create_schedule()

        with patch("api.services.agent_schedules.sync_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )

        schedule.refresh_from_db()
        self.assertEqual(schedule.revision, 2)
        self.assertGreater(schedule.next_run_at, self.scheduled_for)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        process_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_stale_revision_is_ignored_without_advancing(self, process_mock):
        schedule = self._create_schedule()

        process_agent_schedule_trigger_task(
            str(self.agent.id),
            str(schedule.id),
            99,
            self.scheduled_for.isoformat(),
        )

        schedule.refresh_from_db()
        self.assertEqual(schedule.revision, 1)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        process_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.switch_is_active", return_value=False)
    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_event_write_failure_rolls_back_schedule_claim(self, process_mock, _switch_mock):
        schedule = self._create_schedule()

        with patch.object(
            PersistentAgentCronTrigger.objects,
            "create",
            side_effect=RuntimeError("event write failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "event write failed"):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )

        schedule.refresh_from_db()
        self.assertEqual(schedule.revision, 1)
        self.assertEqual(schedule.next_run_at, self.scheduled_for)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        process_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.log_task_quota_exceeded")
    @patch("api.agent.tasks.process_events.switch_is_active", return_value=False)
    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_quota_rejection_still_commits_recurring_rearm(
        self,
        process_mock,
        _switch_mock,
        quota_log_mock,
    ):
        schedule = self._create_schedule()
        quota_error = ValidationError(
            {"quota": ["Task quota exceeded. No remaining task credits."]}
        )

        with patch.object(
            PersistentAgentCronTrigger.objects,
            "create",
            side_effect=quota_error,
        ):
            with patch("api.services.agent_schedules.sync_schedule_entry"):
                with self.captureOnCommitCallbacks(execute=True):
                    process_agent_schedule_trigger_task(
                        str(self.agent.id),
                        str(schedule.id),
                        1,
                        self.scheduled_for.isoformat(),
                    )

        schedule.refresh_from_db()
        self.assertEqual(schedule.revision, 2)
        self.assertGreater(schedule.next_run_at, self.scheduled_for)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        quota_log_mock.assert_called_once()
        process_mock.assert_not_called()

    @patch("api.agent.tasks.process_events.process_agent_events")
    def test_owner_pause_consumes_one_time_occurrence(self, process_mock):
        schedule = self._create_schedule(kind="once")
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={
                "execution_paused": True,
                "execution_pause_reason": "billing_delinquency",
                "execution_paused_at": timezone.now(),
            },
        )

        with patch("api.services.agent_schedules.remove_schedule_entry"):
            with self.captureOnCommitCallbacks(execute=True):
                process_agent_schedule_trigger_task(
                    str(self.agent.id),
                    str(schedule.id),
                    1,
                    self.scheduled_for.isoformat(),
                )

        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.next_run_at)
        self.assertFalse(PersistentAgentCronTrigger.objects.exists())
        process_mock.assert_not_called()
