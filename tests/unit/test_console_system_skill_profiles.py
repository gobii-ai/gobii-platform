import json
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from waffle.models import Flag

from api.agent.system_skills.registry import SystemSkillDefinition, SystemSkillField
from api.models import Organization, OrganizationMembership, SystemSkillProfile, SystemSkillProfileSecret


User = get_user_model()


def _ensure_encryption_key():
    if not os.environ.get("GOBII_ENCRYPTION_KEY"):
        os.environ["GOBII_ENCRYPTION_KEY"] = "test-key-for-system-skill-profiles-123"


def _meta_ads_definition() -> SystemSkillDefinition:
    return SystemSkillDefinition(
        skill_key="meta_ads_platform",
        name="Meta Ads Platform",
        search_summary="Monitor Meta ad accounts and campaign performance.",
        tool_names=("meta_ads",),
        query_aliases=("meta ads", "facebook ads"),
        required_profile_fields=(
            SystemSkillField(key="META_APP_ID", name="App ID"),
            SystemSkillField(key="META_APP_SECRET", name="App Secret"),
            SystemSkillField(key="META_SYSTEM_USER_TOKEN", name="System User Token"),
            SystemSkillField(key="META_AD_ACCOUNT_ID", name="Ad Account ID"),
        ),
        optional_profile_fields=(
            SystemSkillField(
                key="META_API_VERSION",
                name="API Version",
                required=False,
                default="v25.0",
            ),
        ),
        default_values={"META_API_VERSION": "v25.0"},
        setup_instructions="Create a Meta app, a system user token, and an ad account mapping.",
    )


@tag("batch_secrets")
@override_settings(
    GOBII_ENCRYPTION_KEY="test-key-for-system-skill-profiles-123",
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class ConsoleSystemSkillProfileTests(TestCase):
    def setUp(self):
        _ensure_encryption_key()
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        self.user = User.objects.create_user(
            username="profiles-user",
            email="profiles@example.com",
            password="test-pass-123",
        )
        self.org = Organization.objects.create(
            name="Profiles Org",
            slug="profiles-org",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(self.user)

        self.registry_patch = patch.dict(
            "api.agent.system_skills.registry.SYSTEM_SKILL_REGISTRY",
            {"meta_ads_platform": _meta_ads_definition()},
            clear=True,
        )
        self.registry_patch.start()
        self.addCleanup(self.registry_patch.stop)

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def _create_profile(self, profile_key: str, *, is_default: bool | None = None, values: dict | None = None):
        payload = {
            "profile_key": profile_key,
            "label": profile_key.replace("_", " ").title(),
            "values": values
            or {
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
            },
        }
        if is_default is not None:
            payload["is_default"] = is_default
        return self.client.post(
            reverse("console-system-skill-profile-list", args=["meta_ads_platform"]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_create_and_list_personal_profile(self):
        response = self._create_profile("default")

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["owner_scope"], "user")
        self.assertEqual(payload["profile"]["profile_key"], "default")
        self.assertTrue(payload["profile"]["is_default"])
        self.assertTrue(payload["profile"]["complete"])
        self.assertEqual(payload["profile"]["missing_required_keys"], [])

        profile = SystemSkillProfile.objects.get(user=self.user, skill_key="meta_ads_platform", profile_key="default")
        self.assertTrue(profile.is_default)
        self.assertEqual(profile.secrets.count(), 4)
        self.assertEqual(profile.secrets.get(key="META_APP_ID").get_value(), "app-123")

        list_response = self.client.get(reverse("console-system-skill-profile-list", args=["meta_ads_platform"]))
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(len(list_payload["profiles"]), 1)
        self.assertEqual(list_payload["definition"]["skill_key"], "meta_ads_platform")
        self.assertEqual(list_payload["definition"]["default_values"]["META_API_VERSION"], "v25.0")

    def test_system_skill_profiles_page_mounts_personal_context(self):
        response = self.client.get(reverse("console-system-skill-profiles", args=["meta_ads_platform"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-app="system-skill-profiles"')
        self.assertContains(response, 'data-owner-scope="user"')
        self.assertContains(response, 'data-skill-key="meta_ads_platform"')
        self.assertContains(response, 'data-list-url="/console/api/system-skills/meta_ads_platform/profiles/"')

    def test_system_skill_profiles_page_mounts_org_context(self):
        self._set_org_context()

        response = self.client.get(reverse("console-system-skill-profiles", args=["meta_ads_platform"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-owner-scope="organization"')
        self.assertContains(response, 'data-list-url="/console/api/system-skills/meta_ads_platform/profiles/"')

    def test_create_second_profile_and_switch_default(self):
        first_response = self._create_profile("default")
        first_id = first_response.json()["profile"]["id"]

        second_response = self._create_profile(
            "client_b",
            is_default=True,
            values={
                "META_APP_ID": "app-456",
                "META_APP_SECRET": "secret-456",
                "META_SYSTEM_USER_TOKEN": "token-456",
                "META_AD_ACCOUNT_ID": "act_456",
            },
        )
        self.assertEqual(second_response.status_code, 201)

        first_profile = SystemSkillProfile.objects.get(pk=first_id)
        second_profile = SystemSkillProfile.objects.get(profile_key="client_b")
        self.assertFalse(first_profile.is_default)
        self.assertTrue(second_profile.is_default)

        set_default_response = self.client.post(
            reverse("console-system-skill-profile-set-default", args=["meta_ads_platform", first_profile.id]),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(set_default_response.status_code, 200)

        first_profile.refresh_from_db()
        second_profile.refresh_from_db()
        self.assertTrue(first_profile.is_default)
        self.assertFalse(second_profile.is_default)

    def test_patch_profile_updates_values_and_can_clear_optional_field(self):
        create_response = self._create_profile(
            "default",
            values={
                "META_APP_ID": "app-123",
                "META_APP_SECRET": "secret-123",
                "META_SYSTEM_USER_TOKEN": "token-123",
                "META_AD_ACCOUNT_ID": "act_123",
                "META_API_VERSION": "v26.0",
            },
        )
        profile_id = create_response.json()["profile"]["id"]

        response = self.client.patch(
            reverse("console-system-skill-profile-detail", args=["meta_ads_platform", profile_id]),
            data=json.dumps(
                {
                    "label": "Primary Account",
                    "values": {
                        "META_AD_ACCOUNT_ID": "act_999",
                        "META_API_VERSION": None,
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        profile = SystemSkillProfile.objects.get(pk=profile_id)
        self.assertEqual(profile.label, "Primary Account")
        self.assertEqual(profile.secrets.get(key="META_AD_ACCOUNT_ID").get_value(), "act_999")
        self.assertFalse(SystemSkillProfileSecret.objects.filter(profile=profile, key="META_API_VERSION").exists())

    def test_delete_default_profile_promotes_only_remaining_profile(self):
        first_response = self._create_profile("default")
        second_response = self._create_profile(
            "client_b",
            values={
                "META_APP_ID": "app-456",
                "META_APP_SECRET": "secret-456",
                "META_SYSTEM_USER_TOKEN": "token-456",
                "META_AD_ACCOUNT_ID": "act_456",
            },
        )
        first_id = first_response.json()["profile"]["id"]
        second_id = second_response.json()["profile"]["id"]

        delete_response = self.client.delete(
            reverse("console-system-skill-profile-detail", args=["meta_ads_platform", first_id])
        )
        self.assertEqual(delete_response.status_code, 200)

        remaining = SystemSkillProfile.objects.get(pk=second_id)
        self.assertTrue(remaining.is_default)

    def test_profiles_are_scoped_by_console_owner_context(self):
        personal_response = self._create_profile("default")
        self.assertEqual(personal_response.status_code, 201)

        self._set_org_context()
        org_response = self._create_profile("default")
        self.assertEqual(org_response.status_code, 201)
        self.assertTrue(SystemSkillProfile.objects.filter(user=self.user, organization__isnull=True).exists())
        self.assertTrue(SystemSkillProfile.objects.filter(organization=self.org).exists())

        org_list = self.client.get(reverse("console-system-skill-profile-list", args=["meta_ads_platform"]))
        self.assertEqual(org_list.status_code, 200)
        self.assertEqual(org_list.json()["owner_scope"], "organization")
        self.assertEqual(len(org_list.json()["profiles"]), 1)
        self.assertEqual(org_list.json()["profiles"][0]["profile_key"], "default")

    def test_create_rejects_unknown_fields(self):
        response = self.client.post(
            reverse("console-system-skill-profile-list", args=["meta_ads_platform"]),
            data=json.dumps(
                {
                    "profile_key": "default",
                    "label": "Default",
                    "values": {
                        "META_APP_ID": "app-123",
                        "META_UNKNOWN": "nope",
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("values", response.json()["errors"])
