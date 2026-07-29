import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings, tag
from django.urls import reverse

from api.models import UserFingerprintVisit
from api.services.user_fingerprint import (
    FingerprintRetryableError,
    FingerprintTerminalError,
    get_fingerprint_browser_config,
)
from pages.bot_check import (
    build_bot_check_report,
    normalize_client_signals,
    normalize_fingerprint_client_signals,
    normalize_fingerprint_signals,
)


def _client_signals(**overrides):
    values = {
        "webdriver": False,
        "headless_user_agent": False,
        "automation_globals": [],
        "devtools_agent": False,
        "cdp_detected": False,
        "ua_ch_mismatch": False,
        "languages": ["en-US"],
        "platform": "macOS",
        "hardware_concurrency": 8,
        "device_memory": 8,
        "max_touch_points": 0,
        "screen_width": 1440,
        "screen_height": 900,
        "color_depth": 24,
        "cookies_enabled": True,
        "local_storage": True,
        "session_storage": True,
        "timezone": "America/New_York",
        "webgl_vendor": "Google Inc.",
        "webgl_renderer": "ANGLE (Apple)",
        "software_renderer": False,
    }
    values.update(overrides)
    return values


def _server_signals():
    return {
        "ip_address": "198.51.100.24",
        "ip_version": 4,
        "ip_scope": "Private or reserved",
        "user_agent": "Mozilla/5.0 Chrome/126.0",
    }


def _report(client=None, fingerprint=None, fingerprint_status="complete"):
    return build_bot_check_report(
        client or _client_signals(),
        _server_signals(),
        fingerprint or {},
        fingerprint_status=fingerprint_status,
    )


@tag("batch_pages")
class BotCheckScoringTests(SimpleTestCase):
    def test_verified_and_signed_bot_identities_take_precedence(self):
        for identity in ("verified", "signed"):
            with self.subTest(identity=identity):
                report = _report(
                    fingerprint={
                        "bot": "bad",
                        "bot_info": {
                            "identity": identity,
                            "provider": "OpenAI",
                            "name": "ChatGPT Agent",
                        },
                    }
                )
                self.assertEqual(report["verdict"]["code"], "verified_automation")
                self.assertEqual(report["verdict"]["score"], 100)
                self.assertIn("OpenAI ChatGPT Agent", report["verdict"]["summary"])

    def test_spoofed_good_and_bad_bot_precedence(self):
        cases = (
            (
                {"bot": "good", "bot_info": {"identity": "spoofed"}},
                "spoofed_automation",
            ),
            (
                {
                    "bot": "not_detected",
                    "bot_info": {
                        "identity": "unknown",
                        "provider": "Example",
                        "name": "Agent",
                    },
                },
                "recognized_unverified_automation",
            ),
            ({"bot": "good", "bot_info": {}}, "recognized_automation"),
            ({"bot": "bad", "bot_info": {}}, "likely_automated"),
        )
        for fingerprint, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(_report(fingerprint=fingerprint)["verdict"]["code"], expected)

    def test_scoring_thresholds_and_category_caps(self):
        possible = _report(client=_client_signals(automation_globals=["selenium"]))
        likely = _report(client=_client_signals(webdriver=True))
        capped = _report(
            client=_client_signals(
                webdriver=True,
                headless_user_agent=True,
                automation_globals=["selenium"],
                devtools_agent=True,
                cdp_detected=True,
            ),
            fingerprint={
                "developer_tools": True,
                "anti_detect_browser": True,
                "tampering": True,
                "replayed": True,
                "high_activity_device": True,
                "ip_blocklist_attack_source": True,
            },
        )

        self.assertEqual(possible["verdict"]["score"], 35)
        self.assertEqual(possible["verdict"]["code"], "automation_signals")
        self.assertEqual(likely["verdict"]["score"], 70)
        self.assertEqual(likely["verdict"]["code"], "likely_automated")
        self.assertEqual(capped["verdict"]["score"], 100)

    def test_overlapping_cdp_signals_are_deduplicated(self):
        report = _report(
            client=_client_signals(cdp_detected=True),
            fingerprint={"developer_tools": True},
        )
        self.assertEqual(report["verdict"]["score"], 25)
        cdp_check = report["categories"][0]["checks"][-1]
        self.assertEqual(cdp_check["contribution"], 25)

    def test_network_privacy_and_devtools_context_do_not_establish_bot(self):
        report = _report(
            fingerprint={
                "developer_tools": True,
                "vpn": True,
                "tor": True,
                "privacy_settings": True,
                "proxy": True,
                "proxy_type": "residential",
            }
        )
        self.assertEqual(report["verdict"]["score"], 29)
        self.assertEqual(report["verdict"]["code"], "no_strong_signals")

    def test_integrity_activity_and_network_weights(self):
        integrity = _report(
            client=_client_signals(
                ua_ch_mismatch=True,
                languages=[],
                hardware_concurrency=0,
                screen_width=0,
            ),
            fingerprint={"anti_detect_browser": True, "tampering": True},
        )
        activity = _report(
            fingerprint={"replayed": True, "high_activity_device": True}
        )
        network = _report(
            fingerprint={
                "ip_blocklist_attack_source": True,
                "proxy": True,
                "proxy_type": "data_center",
            }
        )

        self.assertEqual(integrity["verdict"]["score"], 35)
        self.assertEqual(activity["verdict"]["score"], 25)
        self.assertEqual(network["verdict"]["score"], 10)

    def test_missing_fingerprint_signals_are_unavailable(self):
        report = _report(fingerprint={}, fingerprint_status="unavailable")
        fingerprint_checks = report["categories"][-1]["checks"]
        self.assertTrue(
            all(
                check["status"] == "unavailable"
                for check in fingerprint_checks
                if check["key"] != "fingerprint_bot"
            )
        )
        self.assertLess(report["coverage"]["completed"], report["coverage"]["total"])

    def test_missing_device_memory_is_displayed_as_unknown(self):
        report = _report(client=_client_signals(device_memory=None))
        hardware_check = next(
            check
            for check in report["categories"][3]["checks"]
            if check["key"] == "hardware"
        )

        self.assertIn("Reported memory: unknown", hardware_check["detail"])
        self.assertNotIn("None GB", hardware_check["detail"])


@tag("batch_pages")
class BotCheckNormalizationTests(SimpleTestCase):
    def test_normalizes_nested_and_flat_fingerprint_signals(self):
        normalized = normalize_fingerprint_signals(
            {
                "bot": {"result": "bad"},
                "bot_type": "browser_automation",
                "bot_info": {
                    "category": "ai_agent",
                    "provider": "OpenAI",
                    "name": "ChatGPT Agent",
                    "identity": "SIGNED",
                },
                "suspect_score": {"score": 14},
                "developer_tools": {"result": True},
                "tampering": {
                    "result": True,
                    "details": {"anti_detect_browser": True},
                },
                "proxy": {
                    "result": True,
                    "details": {"proxy_type": "data_center"},
                },
                "ip_info": {
                    "v4": {
                        "asn": "64500",
                        "asn_name": "Example Network",
                        "asn_type": "hosting",
                        "datacenter_result": True,
                        "geolocation": {
                            "city_name": "New York",
                            "country_name": "United States",
                        },
                    }
                },
                "ip_address": "203.0.113.7",
                "identification": {
                    "visitor_found": True,
                    "confidence": {"score": 0.99},
                },
            }
        )

        self.assertEqual(normalized["bot"], "bad")
        self.assertEqual(normalized["bot_info"]["identity"], "signed")
        self.assertEqual(normalized["suspect_score"], 14)
        self.assertTrue(normalized["developer_tools"])
        self.assertTrue(normalized["anti_detect_browser"])
        self.assertEqual(normalized["proxy_type"], "data_center")
        self.assertEqual(normalized["asn"], "64500")
        self.assertEqual(normalized["city_name"], "New York")
        self.assertEqual(normalized["visitor_confidence"], 0.99)

    def test_malformed_fingerprint_values_become_unavailable(self):
        normalized = normalize_fingerprint_signals(
            {
                "bot": ["bad"],
                "bot_info": "not-an-object",
                "suspect_score": "not-a-number",
                "developer_tools": "yes",
                "ip_info": [],
            }
        )
        self.assertEqual(normalized["bot"], "")
        self.assertEqual(normalized["bot_info"]["identity"], "")
        self.assertIsNone(normalized["suspect_score"])
        self.assertIsNone(normalized["developer_tools"])
        self.assertEqual(normalized["asn"], "")

    def test_client_signal_input_is_bounded_and_typed(self):
        normalized = normalize_client_signals(
            {
                "webdriver": "true",
                "headless_user_agent": True,
                "user_agent": "x" * 3000,
                "platform": "x" * 300,
                "languages": ["en-US"] * 20,
                "automation_globals": ["selenium"] * 20,
                "screen_width": 10**9,
                "extra": "discarded",
            }
        )
        self.assertNotIn("webdriver", normalized)
        self.assertTrue(normalized["headless_user_agent"])
        self.assertNotIn("user_agent", normalized)
        self.assertEqual(len(normalized["platform"]), 128)
        self.assertEqual(len(normalized["languages"]), 10)
        self.assertEqual(len(normalized["automation_globals"]), 12)
        self.assertEqual(normalized["screen_width"], 100000)
        self.assertNotIn("extra", normalized)

    def test_fingerprint_client_summary_is_bounded_and_excludes_identifiers(self):
        normalized = normalize_fingerprint_client_signals(
            {
                "visitor_found": True,
                "confidence": 4,
                "visitor_id": "do-not-accept",
                "request_id": "do-not-accept",
                "integration_error": "agent_error",
            }
        )
        self.assertEqual(
            normalized,
            {
                "visitor_found": True,
                "visitor_confidence": 1,
                "integration_error": "agent_error",
            },
        )


@tag("batch_pages")
class BotCheckViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.start_url = reverse("pages:bot_check_start")
        self.complete_url = reverse("pages:bot_check_complete")

    def _start(self, *, user_agent="Browser A"):
        response = self.client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            HTTP_USER_AGENT=user_agent,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["scan_token"]

    def _complete(self, token, *, user_agent="Browser A", event_id="", client=None):
        return self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": token,
                    "client_signals": client or _client_signals(),
                    "fingerprint_event_id": event_id,
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT=user_agent,
        )

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://fpjscdn.net/v4/embedded-browser-key?apiKey=wrong-key",
        FINGERPRINT_JS_API_KEY="wrong-key",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    def test_v4_cdn_loader_uses_embedded_browser_key_without_legacy_query(self):
        config = get_fingerprint_browser_config()

        self.assertEqual(
            config["loader_url"],
            "https://fpjscdn.net/v4/embedded-browser-key",
        )
        self.assertNotIn("wrong-key", config["loader_url"])
        self.assertNotIn("apiKey=", config["loader_url"])

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://fpjscdn.net/v4/embedded-browser-key",
        FINGERPRINT_JS_API_KEY="",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    def test_v4_cdn_loader_does_not_require_a_duplicate_browser_key_setting(self):
        config = get_fingerprint_browser_config()

        self.assertTrue(config["enabled"])
        self.assertEqual(
            config["loader_url"],
            "https://fpjscdn.net/v4/embedded-browser-key",
        )

    def test_public_page_is_noindex_and_not_in_sitemap(self):
        response = self.client.get(reverse("pages:bot_check"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, "Are you browsing like a bot?")
        self.assertContains(response, 'aria-label="Scan progress"')
        self.assertContains(response, "Run scan again")
        self.assertContains(response, "How to read the report")
        self.assertContains(response, "js/bot_check.js")

        sitemap = self.client.get("/sitemap.xml")
        self.assertNotContains(sitemap, "/bot-check/")

    def test_csrf_is_required_for_scan_start(self):
        client = Client(enforce_csrf_checks=True)
        denied = client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        client.get(reverse("pages:bot_check"))
        csrf = client.cookies["csrftoken"].value
        admitted = client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(admitted.status_code, 200)

    @patch("pages.bot_check.BOT_CHECK_SCAN_RATE_LIMIT_PER_HOUR", 1)
    def test_start_is_rate_limited_by_observed_ip(self):
        first = self.client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            REMOTE_ADDR="198.51.100.10",
        )
        second = self.client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            REMOTE_ADDR="198.51.100.10",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "3600")

    def test_scan_token_is_bound_to_browser_and_ip(self):
        token = self._start(user_agent="Browser A")
        wrong_browser = self._complete(token, user_agent="Browser B")
        self.assertEqual(wrong_browser.status_code, 400)
        self.assertEqual(wrong_browser.json()["code"], "invalid_token")

    @override_settings(FINGERPRINT_JS_ENABLED=False)
    def test_complete_attempts_are_capped_per_scan(self):
        token = self._start()

        for _attempt in range(5):
            self.assertEqual(self._complete(token).status_code, 200)

        exhausted = self._complete(token)
        self.assertEqual(exhausted.status_code, 429)
        self.assertEqual(exhausted.json()["code"], "scan_attempts_exhausted")

    @patch("pages.bot_check.BOT_CHECK_SCAN_TOKEN_MAX_AGE_SECONDS", -1)
    def test_expired_scan_token_is_rejected(self):
        token = self._start()
        response = self._complete(token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["error"].lower())

    def test_invalid_and_oversized_json_are_rejected(self):
        invalid = self.client.post(
            self.complete_url,
            data="{",
            content_type="application/json",
        )
        oversized = self.client.post(
            self.complete_url,
            data=json.dumps({"value": "x" * (33 * 1024)}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(oversized.json()["error"], "The request is too large.")

    def test_complete_rejects_invalid_signal_shape_and_event_id(self):
        token = self._start()
        invalid_signals = self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": token,
                    "client_signals": [],
                    "fingerprint_event_id": "",
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )
        invalid_event = self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": token,
                    "client_signals": _client_signals(),
                    "fingerprint_event_id": "x" * 256,
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )
        self.assertEqual(invalid_signals.status_code, 400)
        self.assertEqual(invalid_event.status_code, 400)

    @override_settings(FINGERPRINT_JS_ENABLED=False)
    def test_local_report_does_not_persist_a_fingerprint_visit(self):
        token = self._start()
        response = self._complete(token)
        self.assertEqual(response.status_code, 200)
        report = response.json()["report"]
        self.assertEqual(report["verdict"]["code"], "no_strong_signals")
        self.assertEqual(report["fingerprint_status"], "unavailable")
        self.assertEqual(UserFingerprintVisit.objects.count(), 0)

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_JS_BEHAVIOR_URL="https://metrics.example",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_fingerprint_success_is_normalized_and_sanitized(self, fetch_mock):
        fetch_mock.return_value = {
            "bot": "bad",
            "event_id": "server-event-secret",
            "identification": {
                "visitor_id": "visitor-secret",
                "confidence": {"score": 0.98},
            },
            "raw_device_attributes": {"canvas": "raw-secret"},
        }
        start = self.client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )
        self.assertTrue(start.json()["fingerprint"]["enabled"])
        self.assertIn("public-browser-key", start.json()["fingerprint"]["loader_url"])

        response = self._complete(
            start.json()["scan_token"],
            event_id="request-123",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["verdict"]["score"], 100)
        response_text = response.content.decode()
        self.assertNotIn("private-server-key", response_text)
        self.assertNotIn("server-event-secret", response_text)
        self.assertNotIn("visitor-secret", response_text)
        self.assertNotIn("raw-secret", response_text)
        self.assertEqual(UserFingerprintVisit.objects.count(), 0)

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="",
    )
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_browser_token_works_without_server_intelligence_key(self, fetch_mock):
        start = self.client.post(
            self.start_url,
            data="{}",
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )
        config = start.json()["fingerprint"]
        self.assertTrue(config["enabled"])
        self.assertFalse(config["server_intelligence_enabled"])

        response = self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": start.json()["scan_token"],
                    "client_signals": _client_signals(),
                    "fingerprint_event_id": "request-123",
                    "fingerprint_client": {
                        "visitor_found": True,
                        "confidence": 0.97,
                        "visitor_id": "visitor-secret",
                    },
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )
        report = response.json()["report"]
        self.assertEqual(report["fingerprint_status"], "browser_only")
        visitor_check = next(
            check
            for check in report["categories"][-1]["checks"]
            if check["key"] == "visitor_confidence"
        )
        self.assertEqual(visitor_check["status"], "info")
        self.assertNotIn("visitor-secret", response.content.decode())
        fetch_mock.assert_not_called()

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="",
    )
    def test_event_id_takes_precedence_over_contradictory_client_error(self):
        token = self._start()
        response = self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": token,
                    "client_signals": _client_signals(),
                    "fingerprint_event_id": "request-123",
                    "fingerprint_client": {
                        "integration_error": "csp_block",
                    },
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["fingerprint_status"], "browser_only")

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_browser_agent_failure_is_reported_without_provider_details(self, fetch_mock):
        token = self._start()
        response = self.client.post(
            self.complete_url,
            data=json.dumps(
                {
                    "scan_token": token,
                    "client_signals": _client_signals(),
                    "fingerprint_event_id": "",
                    "fingerprint_client": {
                        "integration_error": "agent_error",
                        "provider_error": "sensitive details",
                    },
                }
            ),
            content_type="application/json",
            HTTP_USER_AGENT="Browser A",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["fingerprint_status"], "client_error")
        self.assertNotIn("sensitive details", response.content.decode())
        fetch_mock.assert_not_called()

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_missing_browser_event_is_distinguished_from_disabled_configuration(self, fetch_mock):
        response = self._complete(self._start())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["fingerprint_status"], "missing_event")
        fetch_mock.assert_not_called()

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    @patch("pages.bot_check.BOT_CHECK_FINGERPRINT_RETRY_AFTER_MS", 1)
    @patch("pages.bot_check.BOT_CHECK_MAX_COMPLETE_ATTEMPTS", 2)
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_retryable_fingerprint_event_becomes_partial_report(self, fetch_mock):
        fetch_mock.side_effect = FingerprintRetryableError("provider details")
        token = self._start()

        pending = self._complete(token, event_id="request-123")
        partial = self._complete(token, event_id="request-123")

        self.assertEqual(pending.status_code, 202)
        self.assertEqual(pending.json()["status"], "fingerprint_pending")
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()["report"]["fingerprint_status"], "timed_out")
        self.assertNotIn("provider details", partial.content.decode())

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        FINGERPRINT_JS_ENABLED=True,
        FINGERPRINT_JS_URL="https://metrics.example/agent.js",
        FINGERPRINT_JS_API_KEY="public-browser-key",
        FINGERPRINT_SERVER_API_KEY="private-server-key",
    )
    @patch("pages.bot_check.fetch_fingerprint_event_payload")
    def test_terminal_fingerprint_error_returns_sanitized_partial_report(self, fetch_mock):
        fetch_mock.side_effect = FingerprintTerminalError("provider secret")
        token = self._start()
        response = self._complete(token, event_id="request-123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["fingerprint_status"], "error")
        self.assertNotIn("provider secret", response.content.decode())
