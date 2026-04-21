from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings, tag
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError

from api.models import UserFingerprintVisit, UserFingerprintVisitFetchStatusChoices
from api.services.trial_abuse import (
    SIGNAL_SOURCE_SIGNUP,
    capture_request_identity_signals_and_attribution,
)
from api.services.user_fingerprint import (
    enqueue_user_fingerprint_visit_refresh,
    get_fp_bot,
    get_fp_country,
    get_fp_high_activity,
    get_fp_proxy,
    get_fp_suspect_score,
    get_fp_tampering,
    get_fp_tor,
    get_fp_vpn,
    refresh_user_fingerprint_visit,
    stage_user_fingerprint_visit,
)
from api.tasks.fingerprint_tasks import fetch_user_fingerprint_visit_task


User = get_user_model()


@tag("batch_pages")
class UserFingerprintVisitTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _create_user(self, email: str):
        return User.objects.create_user(
            username=email,
            email=email,
            password="pw",
        )

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_capture_request_identity_signals_stages_pending_visit(self, delay_mock):
        user = self._create_user("fingerprint-stage@example.com")
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
                "uga": "GA1.2.111.222",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        visit = UserFingerprintVisit.objects.get(user=user)
        self.assertEqual(visit.source, SIGNAL_SOURCE_SIGNUP)
        self.assertEqual(visit.fingerprint_event_id, "request-456")
        self.assertEqual(visit.fingerprint_visitor_id, "visitor-123")
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PENDING)
        delay_mock.assert_called_once_with(visit.id)

    @override_settings(FINGERPRINT_SERVER_API_KEY="")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_capture_request_identity_signals_marks_visit_not_configured(self, delay_mock):
        user = self._create_user("fingerprint-not-configured@example.com")
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        visit = UserFingerprintVisit.objects.get(user=user)
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.NOT_CONFIGURED)
        delay_mock.assert_not_called()

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_capture_request_identity_signals_deduplicates_same_event(self, delay_mock):
        user = self._create_user("fingerprint-dedupe@example.com")
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        self.assertEqual(UserFingerprintVisit.objects.filter(user=user).count(), 1)
        delay_mock.assert_called_once()

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch(
        "api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay",
        side_effect=KombuOperationalError("broker unavailable"),
    )
    def test_capture_request_identity_signals_tolerates_enqueue_failure(self, delay_mock):
        user = self._create_user("fingerprint-enqueue-failure@example.com")
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            captured = capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        visit = UserFingerprintVisit.objects.get(user=user)
        self.assertEqual(captured["fpjs_request_id"], "request-456")
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.FAILED)
        self.assertIn("Failed to enqueue Fingerprint refresh", visit.error_message)
        delay_mock.assert_called_once_with(visit.id)

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_stage_user_fingerprint_visit_ignores_overlong_event_id(self, delay_mock):
        user = self._create_user("fingerprint-overlong-event@example.com")

        with self.captureOnCommitCallbacks(execute=True):
            visit = stage_user_fingerprint_visit(
                user,
                source=SIGNAL_SOURCE_SIGNUP,
                signal_values={
                    "fpjs_request_id": "r" * 256,
                    "fpjs_visitor_id": "visitor-123",
                },
            )

        self.assertIsNone(visit)
        self.assertFalse(UserFingerprintVisit.objects.filter(user=user).exists())
        delay_mock.assert_not_called()

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_stage_user_fingerprint_visit_discards_overlong_visitor_id(self, delay_mock):
        user = self._create_user("fingerprint-overlong-visitor@example.com")

        with self.captureOnCommitCallbacks(execute=True):
            visit = stage_user_fingerprint_visit(
                user,
                source=SIGNAL_SOURCE_SIGNUP,
                signal_values={
                    "fpjs_request_id": "request-456",
                    "fpjs_visitor_id": "v" * 256,
                },
            )

        self.assertIsNotNone(visit)
        stored_visit = UserFingerprintVisit.objects.get(user=user, fingerprint_event_id="request-456")
        self.assertEqual(stored_visit.fingerprint_visitor_id, "")
        delay_mock.assert_called_once_with(stored_visit.id)

    @override_settings(
        FINGERPRINT_SERVER_API_KEY="fp_secret",
        FINGERPRINT_SERVER_PROCESSING_STALE_SECONDS=60,
    )
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_capture_request_identity_signals_requeues_stale_processing_visit(self, delay_mock):
        user = self._create_user("fingerprint-stale-processing@example.com")
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PROCESSING,
            last_fetch_attempt_at=timezone.now() - timedelta(minutes=10),
        )
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        visit = UserFingerprintVisit.objects.get(user=user, fingerprint_event_id="request-456")
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PENDING)
        delay_mock.assert_called_once_with(visit.id)

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_capture_request_identity_signals_requeues_failed_visit(self, delay_mock):
        user = self._create_user("fingerprint-failed-requeue@example.com")
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
            error_message="Failed to enqueue Fingerprint refresh: broker unavailable",
        )
        request = self.factory.post(
            "/signup",
            {
                "ufp": "visitor-123",
                "ufpr": "request-456",
            },
        )
        request.META["REMOTE_ADDR"] = "198.51.100.24"
        request.COOKIES = {}

        with self.captureOnCommitCallbacks(execute=True):
            capture_request_identity_signals_and_attribution(
                user,
                request,
                source=SIGNAL_SOURCE_SIGNUP,
                include_fpjs=True,
            )

        visit = UserFingerprintVisit.objects.get(user=user, fingerprint_event_id="request-456")
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PENDING)
        self.assertEqual(visit.error_message, "")
        delay_mock.assert_called_once_with(visit.id)

    @override_settings(
        FINGERPRINT_SERVER_API_KEY="fp_secret",
        FINGERPRINT_SERVER_API_URL="https://api.fpjs.io",
        FINGERPRINT_SERVER_API_TIMEOUT_SECONDS=5,
    )
    @patch("api.services.user_fingerprint.requests.get")
    def test_fetch_user_fingerprint_visit_task_stores_normalized_payload(self, requests_get_mock):
        user = self._create_user("fingerprint-fetch@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="nGj7roCJ0YgwXCuABCRN",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )
        payload = {
            "event_id": "server-event-789",
            "timestamp": 1775923616488,
            "sdk": {"platform": "js", "version": "3.12.9"},
            "replayed": False,
            "identification": {
                "visitor_id": "nGj7roCJ0YgwXCuABCRN",
                "confidence": {"score": 0.97, "version": "v1.1"},
                "visitor_found": False,
                "first_seen_at": 1775923616488,
            },
            "ip_address": "2001:ee0:50c4:a910:8154:de8d:ed62:f4b",
            "browser_details": {
                "browser_name": "Chrome",
                "browser_major_version": "146",
                "browser_full_version": "146.0.0",
                "os": "Windows",
                "os_version": "11",
                "device": "Other",
            },
            "bot": "not_detected",
            "ip_blocklist": {
                "email_spam": False,
                "attack_source": True,
                "tor_node": False,
            },
            "ip_info": {
                "v6": {
                    "geolocation": {
                        "timezone": "Asia/Ho_Chi_Minh",
                        "city_name": "Ho Chi Minh City",
                        "country_code": "VN",
                        "country_name": "Vietnam",
                        "subdivisions": [{"name": "Ho Chi Minh City (HCMC)"}],
                    },
                    "asn": "45899",
                    "asn_name": "VNPT Corp",
                    "asn_type": "isp",
                    "datacenter_result": False,
                }
            },
            "suspect_score": 7,
            "tampering": {"result": True, "confidence": "high"},
            "tampering_ml_score": 0.0038,
            "tampering_details": {"anomaly_score": 0, "anti_detect_browser": False},
            "velocity": {"events": {"5_minutes": 1}},
            "vpn": {"result": True, "confidence": "medium"},
            "vpn_methods": {"public_vpn": True},
            "proxy": {"result": True, "confidence": "medium", "details": {"proxy_type": "data_center"}},
            "tor": {"result": False},
            "high_activity_device": True,
        }
        requests_get_mock.return_value = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: payload,
        )

        fetch_user_fingerprint_visit_task(visit.id)

        visit.refresh_from_db()
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.SUCCEEDED)
        self.assertEqual(visit.fetch_attempt_count, 1)
        self.assertEqual(visit.fingerprint_event_id, "request-456")
        self.assertEqual(visit.fingerprint_server_event_id, "server-event-789")
        self.assertEqual(visit.country_code, "VN")
        self.assertEqual(visit.country_name, "Vietnam")
        self.assertEqual(visit.city_name, "Ho Chi Minh City")
        self.assertEqual(visit.subdivision_name, "Ho Chi Minh City (HCMC)")
        self.assertEqual(visit.browser_name, "Chrome")
        self.assertEqual(visit.os, "Windows")
        self.assertEqual(visit.bot, "not_detected")
        self.assertEqual(visit.suspect_score, 7)
        self.assertTrue(visit.vpn)
        self.assertEqual(visit.vpn_confidence, "medium")
        self.assertTrue(visit.proxy)
        self.assertEqual(visit.proxy_type, "data_center")
        self.assertFalse(visit.tor)
        self.assertTrue(visit.tampering)
        self.assertEqual(visit.tampering_confidence, "high")
        self.assertTrue(visit.high_activity_device)
        self.assertTrue(visit.ip_blocklist_attack_source)
        self.assertEqual(visit.asn_name, "VNPT Corp")
        self.assertIsNotNone(visit.event_timestamp)
        self.assertEqual(visit.raw_payload["event_id"], payload["event_id"])

    @override_settings(
        FINGERPRINT_SERVER_API_KEY="fp_secret",
        FINGERPRINT_SERVER_API_URL="https://api.fpjs.io",
        FINGERPRINT_SERVER_API_TIMEOUT_SECONDS=5,
    )
    @patch("api.services.user_fingerprint.requests.get")
    def test_refresh_user_fingerprint_visit_prefers_server_event_id(self, requests_get_mock):
        user = self._create_user("fingerprint-followup-fetch@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_server_event_id="server-event-789",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )
        payload = {
            "event_id": "server-event-789",
            "timestamp": 1775923616488,
            "identification": {
                "visitor_id": "visitor-123",
                "confidence": {"score": 0.97},
                "visitor_found": False,
                "first_seen_at": 1775923616488,
            },
        }
        requests_get_mock.return_value = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: payload,
        )

        refresh_user_fingerprint_visit(visit)

        self.assertEqual(
            requests_get_mock.call_args.args[0],
            "https://api.fpjs.io/v4/events/server-event-789",
        )

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_enqueue_user_fingerprint_visit_refresh_requeues_succeeded_visit(self, delay_mock):
        user = self._create_user("fingerprint-manual-refresh@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.SUCCEEDED,
            error_message="stale error",
            raw_payload={"event_id": "request-456"},
        )

        with self.captureOnCommitCallbacks(execute=True):
            queued = enqueue_user_fingerprint_visit_refresh(visit)

        visit.refresh_from_db()
        self.assertTrue(queued)
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PENDING)
        self.assertEqual(visit.error_message, "")
        delay_mock.assert_called_once_with(visit.id)

    @override_settings(
        FINGERPRINT_SERVER_API_KEY="fp_secret",
        FINGERPRINT_SERVER_PROCESSING_STALE_SECONDS=600,
    )
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.delay")
    def test_enqueue_user_fingerprint_visit_refresh_skips_fresh_processing_visit(self, delay_mock):
        user = self._create_user("fingerprint-already-processing@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PROCESSING,
            last_fetch_attempt_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            queued = enqueue_user_fingerprint_visit_refresh(visit)

        visit.refresh_from_db()
        self.assertFalse(queued)
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PROCESSING)
        delay_mock.assert_not_called()

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.retry")
    @patch("api.tasks.fingerprint_tasks.refresh_user_fingerprint_visit", side_effect=OverflowError("bad timestamp"))
    def test_fetch_user_fingerprint_visit_task_resets_unexpected_error_to_pending(
        self,
        _refresh_mock,
        retry_mock,
    ):
        user = self._create_user("fingerprint-unexpected-error@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )
        retry_mock.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesRegex(RuntimeError, "retry scheduled"):
            fetch_user_fingerprint_visit_task(visit.id)

        visit.refresh_from_db()
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.PENDING)
        self.assertEqual(visit.error_message, "bad timestamp")
        retry_mock.assert_called_once()

    @override_settings(FINGERPRINT_SERVER_API_KEY="fp_secret")
    @patch("api.tasks.fingerprint_tasks.fetch_user_fingerprint_visit_task.retry")
    @patch("api.tasks.fingerprint_tasks.refresh_user_fingerprint_visit", side_effect=OverflowError("bad timestamp"))
    def test_fetch_user_fingerprint_visit_task_marks_final_unexpected_error_failed(
        self,
        _refresh_mock,
        retry_mock,
    ):
        user = self._create_user("fingerprint-final-unexpected-error@example.com")
        visit = UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-456",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )

        fetch_user_fingerprint_visit_task.push_request(
            retries=fetch_user_fingerprint_visit_task.max_retries,
        )
        try:
            fetch_user_fingerprint_visit_task.run(visit.id)
        finally:
            fetch_user_fingerprint_visit_task.pop_request()

        visit.refresh_from_db()
        self.assertEqual(visit.fetch_status, UserFingerprintVisitFetchStatusChoices.FAILED)
        self.assertEqual(visit.error_message, "bad timestamp")
        retry_mock.assert_not_called()

    def test_fp_helpers_return_none_without_succeeded_visit(self):
        user = self._create_user("fingerprint-no-success@example.com")
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-pending",
            fingerprint_visitor_id="visitor-123",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )

        self.assertIsNone(get_fp_suspect_score(user))
        self.assertIsNone(get_fp_country(user))
        self.assertIsNone(get_fp_vpn(user))
        self.assertIsNone(get_fp_tor(user))
        self.assertIsNone(get_fp_proxy(user))
        self.assertIsNone(get_fp_tampering(user))
        self.assertIsNone(get_fp_high_activity(user))
        self.assertIsNone(get_fp_bot(user))

    def test_fp_helpers_use_latest_succeeded_visit(self):
        user = self._create_user("fingerprint-helper-values@example.com")
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-old",
            fingerprint_visitor_id="visitor-old",
            fetch_status=UserFingerprintVisitFetchStatusChoices.SUCCEEDED,
            event_timestamp=timezone.now() - timedelta(days=1),
            suspect_score=2.5,
            country_code="US",
            vpn=False,
            tor=True,
            proxy=False,
            tampering=False,
            high_activity_device=True,
        )
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-new",
            fingerprint_visitor_id="visitor-new",
            fetch_status=UserFingerprintVisitFetchStatusChoices.SUCCEEDED,
            event_timestamp=timezone.now(),
            suspect_score=7.0,
            country_code="VN",
            vpn=True,
            tor=False,
            proxy=True,
            tampering=True,
            high_activity_device=None,
            bot="not_detected",
        )
        UserFingerprintVisit.objects.create(
            user=user,
            source=SIGNAL_SOURCE_SIGNUP,
            fingerprint_event_id="request-failed",
            fingerprint_visitor_id="visitor-failed",
            fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
            event_timestamp=timezone.now() + timedelta(minutes=5),
            suspect_score=99.0,
            country_code="CA",
            vpn=False,
            tor=True,
            proxy=False,
            tampering=False,
            high_activity_device=False,
        )

        self.assertEqual(get_fp_suspect_score(user), 7.0)
        self.assertEqual(get_fp_country(user), "VN")
        self.assertTrue(get_fp_vpn(user))
        self.assertFalse(get_fp_tor(user))
        self.assertTrue(get_fp_proxy(user))
        self.assertTrue(get_fp_tampering(user))
        self.assertIsNone(get_fp_high_activity(user))
        self.assertEqual(get_fp_bot(user), "not_detected")

    def test_fetch_user_fingerprint_visit_task_is_late_acked(self):
        self.assertTrue(fetch_user_fingerprint_visit_task.acks_late)
        self.assertTrue(fetch_user_fingerprint_visit_task.reject_on_worker_lost)
