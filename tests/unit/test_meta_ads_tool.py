import os
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.tools.meta_ads import execute_meta_ads
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.tool_manager import enable_tools, execute_enabled_tool
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentEnabledTool, SystemSkillProfile
from api.services.system_skill_profiles import set_default_system_skill_profile, upsert_system_skill_profile_values


User = get_user_model()


def _ensure_encryption_key():
    if not os.environ.get("GOBII_ENCRYPTION_KEY"):
        os.environ["GOBII_ENCRYPTION_KEY"] = "test-key-for-meta-ads-tool-123"


def _create_test_agent(*, user, name: str) -> PersistentAgent:
    with ExitStack() as stack:
        stack.enter_context(patch.object(BrowserUseAgent, "select_random_proxy", return_value=None))
        browser = BrowserUseAgent.objects.create(user=user, name=f"{name}-browser")
        return PersistentAgent.objects.create(
            user=user,
            name=name,
            charter="",
            browser_use_agent=browser,
        )


def _create_meta_profile(
    *,
    user,
    profile_key: str,
    is_default: bool = False,
    values: dict | None = None,
) -> SystemSkillProfile:
    profile = SystemSkillProfile.objects.create(
        user=user,
        skill_key="meta_ads_platform",
        profile_key=profile_key,
        label=profile_key.replace("_", " ").title(),
        is_default=False,
    )
    upsert_system_skill_profile_values(
        profile,
        values
        or {
            "META_APP_ID": "app-123",
            "META_APP_SECRET": "secret-123",
            "META_SYSTEM_USER_TOKEN": "token-123",
            "META_AD_ACCOUNT_ID": "act_123",
            "META_API_VERSION": "v25.0",
        },
    )
    if is_default:
        set_default_system_skill_profile(profile)
    return profile


def _mock_graph_response(*, payload: dict, status_code: int = 200, headers: dict | None = None):
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = payload
    response.text = ""
    return response


@contextmanager
def _agent_sqlite_test_db():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "state.db")
        token = set_sqlite_db_path(db_path)
        try:
            yield db_path
        finally:
            reset_sqlite_db_path(token)


@tag("batch_mcp_tools")
@override_settings(
    GOBII_ENCRYPTION_KEY="test-key-for-meta-ads-tool-123",
)
class MetaAdsToolTests(TestCase):
    def setUp(self):
        _ensure_encryption_key()
        self.user = User.objects.create_user(
            username="meta-ads-user",
            email="meta-ads@example.com",
            password="test-pass-123",
        )
        self.agent = _create_test_agent(user=self.user, name="Meta Ads Agent")

    def test_execute_meta_ads_requires_profile_setup_when_missing(self):
        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["skill_key"], "meta_ads_platform")
        self.assertEqual(result["selected_profile_key"], "default")
        self.assertIn("META_APP_ID", result["required_fields"])
        self.assertIn("/console/system-skills/meta_ads_platform/profiles/", result["setup_url"])
        self.assertTrue(SystemSkillProfile.objects.filter(user=self.user, profile_key="default").exists())
        self.assertGreater(len(result["setup_steps"]), 0)
        self.assertGreater(len(result["setup_docs"]), 0)

    def test_execute_meta_ads_requires_explicit_profile_when_multiple_and_no_default(self):
        _create_meta_profile(user=self.user, profile_key="client_a")
        _create_meta_profile(user=self.user, profile_key="client_b")
        SystemSkillProfile.objects.filter(user=self.user, skill_key="meta_ads_platform").update(is_default=False)

        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["available_profiles"], ["client_a", "client_b"])
        self.assertIn("Multiple Meta Ads profiles are configured", result["result"])
        self.assertIn("Do not guess", result["agent_guidance"])

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_doctor_performs_live_validation(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.side_effect = [
            _mock_graph_response(
                payload={"data": [{"id": "act_123", "name": "Main Account", "account_status": 1}]},
                headers={"x-app-usage": '{"call_count":1}'},
            ),
            _mock_graph_response(
                payload={"id": "act_123", "name": "Main Account", "account_status": 1},
                headers={"x-ad-account-usage": '{"acc_id_util_pct":1}'},
            ),
        ]

        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["profile_key"], "default")
        self.assertEqual(result["accessible_account_sample_count"], 1)
        self.assertIn("connected", result["result"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_doctor_checks_dataset_quality_when_configured(self, mock_get):
        _create_meta_profile(
            user=self.user,
            profile_key="default",
            is_default=True,
            values={
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
                "META_API_VERSION": "v25.0",
                "META_DATASET_ID": "pixel-123",
            },
        )
        mock_get.side_effect = [
            _mock_graph_response(payload={"data": [{"id": "act_123", "name": "Main Account", "account_status": 1}]}),
            _mock_graph_response(payload={"id": "act_123", "name": "Main Account", "account_status": 1}),
            _mock_graph_response(payload={"web": [{"event_name": "Purchase"}, {"event_name": "Lead"}]}),
        ]

        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dataset_id"], "pixel-123")
        self.assertEqual(result["dataset_quality_event_sample_count"], 2)
        self.assertEqual(mock_get.call_count, 3)

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_accounts_uses_default_profile_and_business_id(self, mock_get):
        _create_meta_profile(
            user=self.user,
            profile_key="default",
            is_default=True,
            values={
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
                "META_API_VERSION": "v25.0",
                "META_BUSINESS_ID": "987654321",
            },
        )
        mock_get.return_value = _mock_graph_response(
            payload={"data": [{"id": "act_123", "name": "Main Account"}]},
            headers={"x-app-usage": '{"call_count":1}'},
        )

        with _agent_sqlite_test_db() as db_path:
            result = execute_meta_ads(self.agent, {"operation": "accounts"})

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["profile_key"], "default")
            self.assertEqual(result["rows_synced"], 1)
            self.assertEqual(result["destination_table"], "meta_ads_raw")
            self.assertIn("x-app-usage", result["rate_limit_headers"])

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT operation, entity_level, entity_id, entity_name FROM meta_ads_raw"
                ).fetchone()
                self.assertEqual(row, ("accounts", "account", "act_123", "Main Account"))
            finally:
                conn.close()

        mock_get.assert_called_once()
        url = mock_get.call_args.args[0]
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("/v25.0/987654321/owned_ad_accounts", url)
        self.assertEqual(params["access_token"], "token-123")
        self.assertEqual(params["limit"], 100)
        self.assertIn("appsecret_proof", params)

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_explicit_profile_key_overrides_default(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        _create_meta_profile(
            user=self.user,
            profile_key="client_b",
            values={
                "META_APP_ID": "app-456",
                "META_APP_SECRET": "secret-456",
                "META_SYSTEM_USER_TOKEN": "token-456",
                "META_AD_ACCOUNT_ID": "act_456",
                "META_API_VERSION": "v25.0",
            },
        )
        mock_get.side_effect = [
            _mock_graph_response(payload={"data": [{"id": "act_456"}]}),
            _mock_graph_response(payload={"id": "act_456", "account_status": 1}),
        ]

        result = execute_meta_ads(self.agent, {"operation": "doctor", "profile_key": "client_b"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["profile_key"], "client_b")
        self.assertEqual(result["default_account_id"], "act_456")

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_performance_snapshot_returns_normalized_rows(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.return_value = _mock_graph_response(
            payload={
                "data": [
                    {
                        "campaign_id": "cmp_1",
                        "campaign_name": "Prospecting",
                        "impressions": "1000",
                        "reach": "700",
                        "spend": "125.50",
                        "clicks": "25",
                        "inline_link_clicks": "20",
                        "actions": [
                            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
                            {"action_type": "lead", "value": "4"},
                        ],
                        "action_values": [
                            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "450.00"},
                        ],
                        "purchase_roas": [
                            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3.59"},
                        ],
                        "date_start": "2026-04-01",
                        "date_stop": "2026-04-07",
                    }
                ]
            },
            headers={"x-fb-ads-insights-throttle": '{"app_id_util_pct":10}'},
        )

        with _agent_sqlite_test_db() as db_path:
            result = execute_meta_ads(
                self.agent,
                {"operation": "performance_snapshot", "level": "campaign", "date_preset": "last_7d"},
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["destination_table"], "meta_ads_performance")
            self.assertEqual(result["rows_synced"], 1)
            self.assertEqual(result["summary"]["purchase_count"], 3.0)
            self.assertEqual(result["summary"]["lead_count"], 4.0)
            self.assertEqual(result["summary"]["purchase_value"], 450.0)
            self.assertEqual(result["summary"]["cost_per_purchase"], 41.8333)
            self.assertEqual(result["summary"]["cost_per_lead"], 31.375)
            self.assertNotIn("normalized_rows", result)
            self.assertIn("sqlite_query_hints", result)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT entity_id, purchase_count, purchase_value, blended_roas, cost_per_purchase
                    FROM meta_ads_performance
                    """
                ).fetchone()
                self.assertEqual(row, ("cmp_1", 3.0, 450.0, 3.59, 41.8333))
            finally:
                conn.close()

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_performance_snapshot_allows_custom_destination_table(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.return_value = _mock_graph_response(
            payload={
                "data": [
                    {
                        "campaign_id": "cmp_1",
                        "campaign_name": "Prospecting",
                        "impressions": "1000",
                        "reach": "700",
                        "spend": "125.50",
                        "clicks": "25",
                        "date_start": "2026-04-01",
                        "date_stop": "2026-04-07",
                    }
                ]
            }
        )

        with _agent_sqlite_test_db() as db_path:
            result = execute_meta_ads(
                self.agent,
                {
                    "operation": "performance_snapshot",
                    "destination_table": "marketing_meta_daily",
                },
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["destination_table"], "marketing_meta_daily")
            conn = sqlite3.connect(db_path)
            try:
                synced = conn.execute("SELECT COUNT(*) FROM marketing_meta_daily").fetchone()[0]
                self.assertEqual(synced, 1)
            finally:
                conn.close()

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_rejects_unsafe_destination_table_name(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.return_value = _mock_graph_response(payload={"data": []})

        with _agent_sqlite_test_db():
            result = execute_meta_ads(
                self.agent,
                {
                    "operation": "performance_snapshot",
                    "destination_table": "meta_ads;DROP TABLE users",
                },
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("Destination table name", result["message"])

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_rejects_reserved_destination_table_name(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.return_value = _mock_graph_response(payload={"data": []})

        with _agent_sqlite_test_db():
            result = execute_meta_ads(
                self.agent,
                {
                    "operation": "accounts",
                    "destination_table": "meta_ads_performance",
                },
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("reserved", result["message"].lower())

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_conversion_quality_returns_quality_rows(self, mock_get):
        _create_meta_profile(
            user=self.user,
            profile_key="default",
            is_default=True,
            values={
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
                "META_API_VERSION": "v25.0",
                "META_DATASET_ID": "pixel-123",
            },
        )
        mock_get.return_value = _mock_graph_response(
            payload={
                "web": [
                    {
                        "event_name": "Purchase",
                        "event_match_quality": {
                            "composite_score": 7.5,
                            "diagnostics": [{"name": "Mismatch"}],
                        },
                        "acr": {"percentage": 22.4},
                        "data_freshness": {"upload_frequency": "real_time"},
                    },
                    {
                        "event_name": "Lead",
                        "event_match_quality": {"composite_score": 6.5, "diagnostics": []},
                        "event_potential_aly_acr_increase": {"percentage": 14.2},
                        "data_freshness": {"upload_frequency": "hourly"},
                    },
                ]
            }
        )

        with _agent_sqlite_test_db() as db_path:
            result = execute_meta_ads(self.agent, {"operation": "conversion_quality"})

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["dataset_id"], "pixel-123")
            self.assertEqual(result["destination_table"], "meta_ads_conversion_quality")
            self.assertEqual(result["summary"]["event_count"], 2)
            self.assertEqual(result["summary"]["events_with_diagnostics"], 1)
            self.assertEqual(result["summary"]["realtime_event_count"], 1)
            self.assertEqual(result["summary"]["avg_event_match_quality_score"], 7.0)
            self.assertNotIn("quality_rows", result)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT event_name, event_match_quality_score, diagnostics_count, upload_frequency
                    FROM meta_ads_conversion_quality
                    ORDER BY event_name ASC
                    """
                ).fetchone()
                self.assertEqual(row, ("Lead", 6.5, 0, "hourly"))
            finally:
                conn.close()

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_enabled_tool_records_usage_for_hidden_meta_tool(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        enable_result = enable_tools(self.agent, ["meta_ads"], include_hidden_builtin=True)
        self.assertEqual(enable_result["status"], "success")
        mock_get.side_effect = [
            _mock_graph_response(payload={"data": [{"id": "act_123"}]}),
            _mock_graph_response(payload={"id": "act_123", "account_status": 1}),
        ]

        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="meta_ads")
        self.assertEqual(row.usage_count, 0)
        self.assertIsNone(row.last_used_at)

        result = execute_enabled_tool(self.agent, "meta_ads", {"operation": "doctor"})

        self.assertEqual(result["status"], "success")
        row.refresh_from_db()
        self.assertEqual(row.usage_count, 1)
        self.assertIsNotNone(row.last_used_at)

    @patch("api.agent.tools.meta_ads.requests.get")
    def test_execute_meta_ads_returns_action_required_when_live_auth_check_fails(self, mock_get):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        mock_get.return_value = _mock_graph_response(
            payload={"error": {"message": "Invalid OAuth access token.", "code": 190}},
            status_code=400,
        )

        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "action_required")
        self.assertIn("Invalid OAuth access token", result["auth_error"])
        self.assertIn("developer registration", result["agent_guidance"].lower())
        self.assertEqual(result["selected_profile_key"], "default")
