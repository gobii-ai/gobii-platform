import os
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from api.agent.tools.meta_ads import execute_meta_ads
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
        self.assertIn("META_APP_ID", result["required_fields"])
        self.assertIn("/console/system-skills/meta_ads_platform/profiles/", result["setup_url"])

    def test_execute_meta_ads_requires_explicit_profile_when_multiple_and_no_default(self):
        _create_meta_profile(user=self.user, profile_key="client_a")
        _create_meta_profile(user=self.user, profile_key="client_b")
        SystemSkillProfile.objects.filter(user=self.user, skill_key="meta_ads_platform").update(is_default=False)

        result = execute_meta_ads(self.agent, {"operation": "doctor"})

        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["available_profiles"], ["client_a", "client_b"])
        self.assertIn("Multiple Meta Ads profiles are configured", result["result"])

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

        result = execute_meta_ads(self.agent, {"operation": "accounts"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["profile_key"], "default")
        self.assertEqual(result["rows"][0]["id"], "act_123")
        self.assertIn("x-app-usage", result["rate_limit_headers"])

        mock_get.assert_called_once()
        url = mock_get.call_args.args[0]
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("/v25.0/987654321/owned_ad_accounts", url)
        self.assertEqual(params["access_token"], "token-123")
        self.assertEqual(params["limit"], 100)
        self.assertIn("appsecret_proof", params)

    def test_execute_meta_ads_explicit_profile_key_overrides_default(self):
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

        result = execute_meta_ads(self.agent, {"operation": "doctor", "profile_key": "client_b"})

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["profile_key"], "client_b")
        self.assertEqual(result["default_account_id"], "act_456")

    def test_execute_enabled_tool_records_usage_for_hidden_meta_tool(self):
        _create_meta_profile(user=self.user, profile_key="default", is_default=True)
        enable_result = enable_tools(self.agent, ["meta_ads"], include_hidden_builtin=True)
        self.assertEqual(enable_result["status"], "success")

        row = PersistentAgentEnabledTool.objects.get(agent=self.agent, tool_full_name="meta_ads")
        self.assertEqual(row.usage_count, 0)
        self.assertIsNone(row.last_used_at)

        result = execute_enabled_tool(self.agent, "meta_ads", {"operation": "doctor"})

        self.assertEqual(result["status"], "success")
        row.refresh_from_db()
        self.assertEqual(row.usage_count, 1)
        self.assertIsNotNone(row.last_used_at)
