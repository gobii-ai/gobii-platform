from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings, tag

from api.models import UserFingerprintVisit, UserFingerprintVisitFetchStatusChoices
from api.services.trial_abuse import (
    SIGNAL_SOURCE_SIGNUP,
    capture_request_identity_signals_and_attribution,
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
            fingerprint_event_id="1775923616477.ugM7EF",
            fingerprint_visitor_id="nGj7roCJ0YgwXCuABCRN",
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
        )
        payload = {
            "event_id": "1775923616477.ugM7EF",
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
