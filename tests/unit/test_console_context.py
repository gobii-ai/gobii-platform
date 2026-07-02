import uuid
import json
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings, tag

from waffle.models import Flag
from waffle.testutils import override_flag

from agents.services import PretrainedWorkerTemplateService
from api.models import (
    Organization,
    OrganizationMembership,
    AgentOwnerCustomInstructions,
    AgentCollaborator,
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentTemplate,
    BrowserUseAgentTask,
    TaskCredit,
    OrganizationInvite,
)
from console.agent_creation import (
    AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY,
    AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE,
    AGENT_TEMPLATE_SOURCE_SESSION_KEY,
)
from api.services.organization_permissions import ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS
from django.utils import timezone
from constants.plans import PlanNamesChoices
from tests.utils.llm_seed import get_intelligence_tier


User = get_user_model()


@tag("batch_console_context")
@override_settings(
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class OrganizationCreateAPITests(TestCase):
    def setUp(self):
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})
        self.user = User.objects.create_user(username="org-create", email="org-create@example.com", password="pw")
        self.client.force_login(self.user)

    def test_create_organization_api_creates_owner_membership_and_switches_context(self):
        resp = self.client.post(
            reverse("console-organization-create"),
            data=json.dumps({"name": "New Ops Org"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 201)
        payload = resp.json()
        org = Organization.objects.get(name="New Ops Org")
        self.assertEqual(payload["organization"]["id"], str(org.id))
        self.assertEqual(payload["context"], {
            "type": "organization",
            "id": str(org.id),
            "name": "New Ops Org",
            "canCreateAgents": True,
        })
        self.assertTrue(
            OrganizationMembership.objects.filter(
                org=org,
                user=self.user,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists()
        )
        session = self.client.session
        self.assertEqual(session["context_type"], "organization")
        self.assertEqual(session["context_id"], str(org.id))
        self.assertEqual(session["context_name"], "New Ops Org")

    def test_create_organization_api_requires_feature_flag(self):
        with override_flag("organizations", active=False):
            resp = self.client.post(
                reverse("console-organization-create"),
                data=json.dumps({"name": "Disabled Org"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Organization.objects.filter(name="Disabled Org").exists())


@tag("batch_console_context")
@override_settings(
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class CurrentOrganizationAPITests(TestCase):
    def setUp(self):
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})
        self.owner = User.objects.create_user(username="org-owner", email="owner@example.com", password="pw")
        self.admin = User.objects.create_user(username="org-admin", email="admin@example.com", password="pw")
        self.member = User.objects.create_user(username="org-member", email="member@example.com", password="pw")
        self.org = Organization.objects.create(
            name="Acme Team",
            slug="acme-team",
            plan="free",
            created_by=self.owner,
        )
        billing = self.org.billing
        billing.purchased_seats = 5
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.admin,
            role=OrganizationMembership.OrgRole.ADMIN,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

    def _login_in_org_context(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def _create_org_agent(self, *, name="Ops Agent", preferred_llm_tier=None):
        browser_agent = BrowserUseAgent.objects.create(user=self.owner, name=f"{name} Browser")
        agent_kwargs = {
            "user": self.owner,
            "organization": self.org,
            "name": name,
            "charter": "Keep the org on track.",
            "browser_use_agent": browser_agent,
        }
        if preferred_llm_tier is not None:
            agent_kwargs["preferred_llm_tier"] = preferred_llm_tier
        return PersistentAgent.objects.create(**agent_kwargs)

    def _create_org_template(self, *, code="org-context-template", preferred_llm_tier=None):
        template_kwargs = {
            "code": code,
            "organization": self.org,
            "display_name": "Org Context Template",
            "tagline": "Private workflow",
            "description": "Only members of this organization can use it.",
            "charter": "Run the private organization workflow.",
            "base_schedule": "@daily",
            "category": "Operations",
            "is_active": True,
        }
        if preferred_llm_tier is not None:
            template_kwargs["preferred_llm_tier"] = preferred_llm_tier
        return PersistentAgentTemplate.objects.create(**template_kwargs)

    def test_current_organization_api_lists_members_and_invites_for_org_context(self):
        self._login_in_org_context(self.owner)

        resp = self.client.get(reverse("console-current-organization"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["organization"]["id"], str(self.org.id))
        self.assertFalse(payload["organization"]["membersCanCreateAgents"])
        self.assertEqual(payload["viewer"]["role"], OrganizationMembership.OrgRole.OWNER)
        self.assertTrue(payload["viewer"]["canEditOrganization"])
        self.assertTrue(payload["viewer"]["canEditMemberAgentCreation"])
        self.assertTrue(payload["viewer"]["canManageMembers"])
        self.assertEqual({member["email"] for member in payload["members"]}, {
            "owner@example.com",
            "admin@example.com",
            "member@example.com",
        })

    def test_current_organization_api_requires_org_context(self):
        self.client.force_login(self.owner)

        resp = self.client.get(reverse("console-current-organization"))

        self.assertEqual(resp.status_code, 404)

    @override_settings(AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS=123)
    def test_current_organization_api_includes_custom_instructions_settings(self):
        AgentOwnerCustomInstructions.objects.create(
            organization=self.org,
            instructions="Use Acme's support voice.",
            updated_by=self.owner,
        )
        self._login_in_org_context(self.owner)

        resp = self.client.get(reverse("console-current-organization"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["organization"]["customInstructions"], "Use Acme's support voice.")
        self.assertEqual(payload["organization"]["customInstructionsMaxChars"], 123)
        self.assertTrue(payload["viewer"]["canEditCustomInstructions"])

    def test_config_authority_roles_can_update_custom_instructions(self):
        solutions_partner = User.objects.create_user(
            username="org-solutions",
            email="solutions@example.com",
            password="pw",
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=solutions_partner,
            role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        for user, label in (
            (self.owner, "owner"),
            (self.admin, "admin"),
            (solutions_partner, "solutions"),
        ):
            with self.subTest(role=label):
                self._login_in_org_context(user)
                resp = self.client.patch(
                    reverse("console-current-organization"),
                    data=json.dumps({"customInstructions": f"  {label} line\r\nSecond line  "}),
                    content_type="application/json",
                )

                self.assertEqual(resp.status_code, 200)
                instructions = AgentOwnerCustomInstructions.objects.get(organization=self.org)
                self.assertEqual(instructions.instructions, f"{label} line\nSecond line")
                self.assertEqual(instructions.updated_by, user)
                self.assertEqual(resp.json()["organization"]["customInstructions"], f"{label} line\nSecond line")

    def test_config_authority_roles_can_update_member_agent_creation_setting(self):
        self._login_in_org_context(self.admin)

        enable_resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"membersCanCreateAgents": True}),
            content_type="application/json",
        )

        self.assertEqual(enable_resp.status_code, 200)
        self.org.refresh_from_db()
        self.assertTrue(self.org.org_settings[ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS])
        self.assertTrue(enable_resp.json()["organization"]["membersCanCreateAgents"])

        disable_resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"membersCanCreateAgents": False}),
            content_type="application/json",
        )

        self.assertEqual(disable_resp.status_code, 200)
        self.org.refresh_from_db()
        self.assertNotIn(ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS, self.org.org_settings)
        self.assertFalse(disable_resp.json()["organization"]["membersCanCreateAgents"])

    def test_member_cannot_update_member_agent_creation_setting(self):
        self._login_in_org_context(self.member)

        resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"membersCanCreateAgents": True}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.org.refresh_from_db()
        self.assertNotIn(ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS, self.org.org_settings)

    def test_member_and_viewer_cannot_update_custom_instructions(self):
        viewer = User.objects.create_user(username="org-viewer", email="viewer@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=self.org,
            user=viewer,
            role=OrganizationMembership.OrgRole.VIEWER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        for user, label in ((self.member, "member"), (viewer, "viewer")):
            with self.subTest(role=label):
                self._login_in_org_context(user)
                resp = self.client.patch(
                    reverse("console-current-organization"),
                    data=json.dumps({"customInstructions": "Blocked"}),
                    content_type="application/json",
                )

                self.assertEqual(resp.status_code, 403)
                self.assertFalse(AgentOwnerCustomInstructions.objects.filter(organization=self.org).exists())

    @override_settings(AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS=5)
    def test_custom_instructions_rejects_over_limit_text(self):
        self._login_in_org_context(self.admin)

        resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"customInstructions": "123456"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("customInstructions", resp.json()["errors"])
        self.assertFalse(AgentOwnerCustomInstructions.objects.filter(organization=self.org).exists())

    def test_empty_custom_instructions_clears_existing_row(self):
        AgentOwnerCustomInstructions.objects.create(
            organization=self.org,
            instructions="Existing instructions",
            updated_by=self.owner,
        )
        self._login_in_org_context(self.admin)

        resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"customInstructions": " \r\n "}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AgentOwnerCustomInstructions.objects.filter(organization=self.org).exists())
        self.assertEqual(resp.json()["organization"]["customInstructions"], "")

    def test_organization_and_personal_custom_instructions_are_isolated(self):
        self._login_in_org_context(self.owner)

        org_resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"customInstructions": "Organization-only instructions"}),
            content_type="application/json",
        )

        self.assertEqual(org_resp.status_code, 200)
        self.assertEqual(
            org_resp.json()["organization"]["customInstructions"],
            "Organization-only instructions",
        )
        profile_resp = self.client.get(reverse("console_user_profile"))
        self.assertEqual(profile_resp.status_code, 200)
        self.assertEqual(profile_resp.json()["customInstructions"], "")

        personal_resp = self.client.patch(
            reverse("console_user_profile"),
            data=json.dumps({"customInstructions": "Personal-only instructions"}),
            content_type="application/json",
        )

        self.assertEqual(personal_resp.status_code, 200)
        self.assertEqual(personal_resp.json()["customInstructions"], "Personal-only instructions")
        self.assertEqual(
            self.client.get(reverse("console-current-organization")).json()["organization"]["customInstructions"],
            "Organization-only instructions",
        )

        clear_personal_resp = self.client.patch(
            reverse("console_user_profile"),
            data=json.dumps({"customInstructions": ""}),
            content_type="application/json",
        )

        self.assertEqual(clear_personal_resp.status_code, 200)
        self.assertEqual(clear_personal_resp.json()["customInstructions"], "")
        self.assertEqual(
            self.client.get(reverse("console-current-organization")).json()["organization"]["customInstructions"],
            "Organization-only instructions",
        )

    def test_current_organization_templates_api_lists_templates_for_active_members(self):
        template = self._create_org_template()
        self._login_in_org_context(self.member)

        resp = self.client.get(reverse("console-current-organization-templates"))

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["viewer"]["canManageTemplates"])
        self.assertEqual(payload["sourceAgents"], [])
        self.assertEqual([item["id"] for item in payload["templates"]], [str(template.id)])
        self.assertEqual(payload["templates"][0]["charter"], template.charter)

    def test_current_organization_template_launch_seeds_org_spawn_session(self):
        template = self._create_org_template(preferred_llm_tier=get_intelligence_tier("premium"))
        self.org.org_settings = {ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS: True}
        self.org.save(update_fields=["org_settings"])
        self._login_in_org_context(self.member)

        resp = self.client.post(
            reverse("console-current-organization-template-launch", kwargs={"template_id": template.id}),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["redirectUrl"], "/app/agents/new?spawn=1")
        session = self.client.session
        self.assertEqual(session["context_type"], "organization")
        self.assertEqual(session["context_id"], str(self.org.id))
        self.assertEqual(session["agent_charter"], template.charter)
        self.assertEqual(session["agent_charter_source"], "template")
        self.assertEqual(session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY], template.code)
        self.assertEqual(session[AGENT_TEMPLATE_SOURCE_SESSION_KEY], AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE)
        self.assertEqual(session[AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY], str(self.org.id))
        self.assertEqual(session["agent_preferred_llm_tier"], "premium")

    def test_current_organization_template_launch_rejects_member_without_creation_setting(self):
        template = self._create_org_template()
        self._login_in_org_context(self.member)

        resp = self.client.post(
            reverse("console-current-organization-template-launch", kwargs={"template_id": template.id}),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertIn("permission to create agents", resp.json()["error"])
        session = self.client.session
        self.assertNotIn(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY, session)
        self.assertNotIn(AGENT_TEMPLATE_SOURCE_SESSION_KEY, session)

    @patch("api.services.template_clone.TemplateCloneService._generate_template")
    def test_current_organization_templates_api_allows_manage_roles_to_create_and_deactivate(self, mock_generate_template):
        source_agent = self._create_org_agent(preferred_llm_tier=get_intelligence_tier("max"))
        mock_generate_template.return_value = {
            "display_name": "Generated Org Template",
            "tagline": "Reusable workflow",
            "description": "A reusable private workflow.",
            "charter": "Run this reusable private workflow.",
            "base_schedule": "@daily",
            "schedule_jitter_minutes": 0,
            "default_tools": [],
            "recommended_contact_channel": "email",
            "category": "Operations",
            "event_triggers": [],
        }
        self._login_in_org_context(self.admin)

        create_resp = self.client.post(
            reverse("console-current-organization-templates"),
            data=json.dumps({"sourceAgentId": str(source_agent.id)}),
            content_type="application/json",
        )

        self.assertEqual(create_resp.status_code, 201)
        template = PersistentAgentTemplate.objects.get(source_agent=source_agent)
        self.assertEqual(template.organization, self.org)
        self.assertIsNone(template.public_profile_id)
        self.assertEqual(template.preferred_llm_tier.key, "max")
        created_payload = create_resp.json()
        self.assertIn(str(template.id), [item["id"] for item in created_payload["templates"]])
        created_template = next(item for item in created_payload["templates"] if item["id"] == str(template.id))
        self.assertEqual(created_template["preferredLlmTier"], "max")

        delete_resp = self.client.delete(
            reverse("console-current-organization-template-detail", kwargs={"template_id": template.id}),
        )

        self.assertEqual(delete_resp.status_code, 200)
        template.refresh_from_db()
        self.assertFalse(template.is_active)

    def test_current_organization_templates_api_rejects_create_for_member_role(self):
        source_agent = self._create_org_agent()
        self._login_in_org_context(self.member)

        resp = self.client.post(
            reverse("console-current-organization-templates"),
            data=json.dumps({"sourceAgentId": str(source_agent.id)}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PersistentAgentTemplate.objects.filter(source_agent=source_agent).exists())

    def test_current_organization_templates_api_creates_scratch_template(self):
        get_intelligence_tier("premium")
        self._login_in_org_context(self.admin)

        resp = self.client.post(
            reverse("console-current-organization-templates"),
            data=json.dumps({
                "name": "Escalation Helper",
                "tagline": "Keeps customer escalations moving.",
                "charter": "Review open escalations and draft next actions.",
                "preferredLlmTier": "premium",
            }),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 201)
        template = PersistentAgentTemplate.objects.get(display_name="Escalation Helper")
        self.assertEqual(template.organization, self.org)
        self.assertIsNone(template.source_agent_id)
        self.assertEqual(template.tagline, "Keeps customer escalations moving.")
        self.assertEqual(template.description, "Keeps customer escalations moving.")
        self.assertEqual(template.charter, "Review open escalations and draft next actions.")
        self.assertEqual(template.category, "Custom")
        self.assertEqual(template.preferred_llm_tier.key, "premium")
        payload_template = next(item for item in resp.json()["templates"] if item["id"] == str(template.id))
        self.assertEqual(payload_template["preferredLlmTier"], "premium")

    def test_current_organization_template_patch_updates_editor_fields(self):
        template = self._create_org_template(preferred_llm_tier=get_intelligence_tier("standard"))
        get_intelligence_tier("max")
        self._login_in_org_context(self.admin)

        patch_resp = self.client.patch(
            reverse("console-current-organization-template-detail", kwargs={"template_id": template.id}),
            data=json.dumps({
                "name": "Updated Template",
                "tagline": "Updated short description.",
                "charter": "Use these updated template instructions.",
                "preferredLlmTier": "max",
            }),
            content_type="application/json",
        )

        self.assertEqual(patch_resp.status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.display_name, "Updated Template")
        self.assertEqual(template.tagline, "Updated short description.")
        self.assertEqual(template.description, "Updated short description.")
        self.assertEqual(template.charter, "Use these updated template instructions.")
        self.assertEqual(template.preferred_llm_tier.key, "max")
        self.assertEqual(patch_resp.json()["template"]["preferredLlmTier"], "max")
        self.assertEqual(patch_resp.json()["template"]["charter"], "Use these updated template instructions.")

    def test_owner_can_update_organization_name(self):
        self._login_in_org_context(self.owner)

        resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"name": "Renamed Team"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Renamed Team")
        session = self.client.session
        self.assertEqual(session["context_name"], "Renamed Team")

    def test_admin_cannot_update_organization_name(self):
        self._login_in_org_context(self.admin)

        resp = self.client.patch(
            reverse("console-current-organization"),
            data=json.dumps({"name": "Blocked Name"}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Acme Team")

    def test_admin_can_invite_member_and_update_non_owner_role(self):
        self._login_in_org_context(self.admin)

        invite_resp = self.client.post(
            reverse("console-current-organization-invites"),
            data=json.dumps({"email": "new@example.com", "role": OrganizationMembership.OrgRole.MEMBER}),
            content_type="application/json",
        )
        role_resp = self.client.patch(
            reverse("console-current-organization-member-detail", kwargs={"user_id": self.member.id}),
            data=json.dumps({"role": OrganizationMembership.OrgRole.VIEWER}),
            content_type="application/json",
        )

        self.assertEqual(invite_resp.status_code, 201)
        self.assertTrue(OrganizationInvite.objects.filter(org=self.org, email="new@example.com").exists())
        self.assertEqual(role_resp.status_code, 200)
        membership = OrganizationMembership.objects.get(org=self.org, user=self.member)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.VIEWER)

    def test_owner_can_invite_solutions_partner_without_available_seats(self):
        seatless_org = Organization.objects.create(
            name="Seatless Team",
            slug="seatless-team",
            plan="free",
            created_by=self.owner,
        )
        OrganizationMembership.objects.create(
            org=seatless_org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(self.owner)
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(seatless_org.id)
        session["context_name"] = seatless_org.name
        session.save()

        standard_resp = self.client.post(
            reverse("console-current-organization-invites"),
            data=json.dumps({"email": "standard@example.com", "role": OrganizationMembership.OrgRole.MEMBER}),
            content_type="application/json",
        )
        partner_resp = self.client.post(
            reverse("console-current-organization-invites"),
            data=json.dumps({"email": "partner@example.com", "role": OrganizationMembership.OrgRole.SOLUTIONS_PARTNER}),
            content_type="application/json",
        )

        self.assertEqual(standard_resp.status_code, 400)
        self.assertEqual(partner_resp.status_code, 201)
        self.assertTrue(
            OrganizationInvite.objects.filter(
                org=seatless_org,
                email="partner@example.com",
                role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            ).exists()
        )

    def test_admin_cannot_modify_owner_role(self):
        self._login_in_org_context(self.admin)

        resp = self.client.patch(
            reverse("console-current-organization-member-detail", kwargs={"user_id": self.owner.id}),
            data=json.dumps({"role": OrganizationMembership.OrgRole.MEMBER}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        membership = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.OWNER)

    def test_owner_can_remove_member_and_revoke_invite(self):
        self._login_in_org_context(self.owner)
        invite = OrganizationInvite.objects.create(
            org=self.org,
            email="pending@example.com",
            role=OrganizationMembership.OrgRole.MEMBER,
            token="pending-token",
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )

        remove_resp = self.client.delete(
            reverse("console-current-organization-member-detail", kwargs={"user_id": self.member.id}),
        )
        revoke_resp = self.client.delete(
            reverse("console-current-organization-invite-detail", kwargs={"token": invite.token}),
        )

        self.assertEqual(remove_resp.status_code, 200)
        self.assertEqual(revoke_resp.status_code, 200)
        self.assertEqual(
            OrganizationMembership.objects.get(org=self.org, user=self.member).status,
            OrganizationMembership.OrgStatus.REMOVED,
        )
        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)

    def test_owner_can_resend_invite_from_current_organization_api(self):
        self._login_in_org_context(self.owner)
        invite = OrganizationInvite.objects.create(
            org=self.org,
            email="pending@example.com",
            role=OrganizationMembership.OrgRole.MEMBER,
            token="pending-resend-token",
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )
        original_sent_at = timezone.now() - timezone.timedelta(days=1)
        OrganizationInvite.objects.filter(pk=invite.pk).update(sent_at=original_sent_at)

        resp = self.client.post(
            reverse("console-current-organization-invite-resend", kwargs={"token": invite.token}),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        invite.refresh_from_db()
        self.assertGreater(invite.sent_at, original_sent_at)
        payload = resp.json()
        resent_invite = next(item for item in payload["pendingInvites"] if item["token"] == invite.token)
        self.assertEqual(resent_invite["email"], "pending@example.com")

    def test_member_cannot_resend_invite_from_current_organization_api(self):
        self._login_in_org_context(self.member)
        invite = OrganizationInvite.objects.create(
            org=self.org,
            email="pending@example.com",
            role=OrganizationMembership.OrgRole.MEMBER,
            token="pending-resend-forbidden-token",
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )

        resp = self.client.post(
            reverse("console-current-organization-invite-resend", kwargs={"token": invite.token}),
        )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    },
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)

@tag('batch_console_context')
class ConsoleContextTests(TestCase):
    def setUp(self):
        # Enable organizations feature flag for all requests
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        # Users
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
        self.stranger = User.objects.create_user(username="stranger", email="stranger@example.com", password="pw")

        # Org and membership
        self.org = Organization.objects.create(
            name="Acme, Inc.", slug="acme", plan="free", created_by=self.owner
        )
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.owner,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        billing = self.org.billing
        billing.purchased_seats = 3
        billing.subscription = PlanNamesChoices.ORG_TEAM.value
        billing.save(update_fields=["purchased_seats", "subscription"])
        owner_billing = self.owner.billing
        owner_billing.subscription = PlanNamesChoices.STARTUP.value
        owner_billing.save(update_fields=["subscription"])

        # Agents
        self.personal_browser = BrowserUseAgent.objects.create(user=self.owner, name="Personal Agent")
        self.personal_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=None,
            name="Personal PA",
            charter="",
            browser_use_agent=self.personal_browser,
        )

        self.org_browser = BrowserUseAgent.objects.create(user=self.owner, name="Org Agent")
        self.org_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.org,
            name="Org PA",
            charter="",
            browser_use_agent=self.org_browser,
        )

        # Login owner by default
        assert self.client.login(username="owner", password="pw")

    def _set_personal_context(self):
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.owner.id)
        session["context_name"] = self.owner.get_full_name() or self.owner.username
        session.save()

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def test_switch_context_invalid_org_override_format_returns_403(self):
        resp = self.client.get(
            reverse("switch_context"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID="not-a-uuid",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "Invalid context override.")

    def test_switch_context_for_agent_returns_org_context_without_persisting_session(self):
        self._set_personal_context()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.org_agent.id)},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("context", {}).get("type"), "organization")
        self.assertEqual(payload.get("context", {}).get("id"), str(self.org.id))

        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    def test_switch_context_for_agent_overrides_stale_context_headers(self):
        self._set_personal_context()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.org_agent.id)},
            HTTP_X_GOBII_CONTEXT_TYPE="personal",
            HTTP_X_GOBII_CONTEXT_ID=str(self.owner.id),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("context", {}).get("type"), "organization")
        self.assertEqual(payload.get("context", {}).get("id"), str(self.org.id))

    def test_switch_context_for_agent_forbidden_without_access(self):
        self.client.logout()
        assert self.client.login(username="stranger", password="pw")
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.stranger.id)
        session["context_name"] = self.stranger.username
        session.save()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.org_agent.id)},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "Not permitted")

    def test_roster_for_deleted_agent_returns_remaining_agents(self):
        extra_browser = BrowserUseAgent.objects.create(user=self.owner, name="Org Agent Two")
        extra_org_agent = PersistentAgent.objects.create(
            user=self.owner,
            organization=self.org,
            name="Org PA Two",
            charter="",
            browser_use_agent=extra_browser,
        )
        self.org_agent.soft_delete()
        self._set_personal_context()

        resp = self.client.get(
            reverse("console_agent_roster"),
            {"for_agent": str(self.org_agent.id)},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("requested_agent_status"), "deleted")
        self.assertEqual(payload.get("context", {}).get("type"), "organization")
        self.assertEqual(payload.get("context", {}).get("id"), str(self.org.id))
        roster_ids = {entry["id"] for entry in payload.get("agents", [])}
        self.assertIn(str(extra_org_agent.id), roster_ids)
        self.assertNotIn(str(self.org_agent.id), roster_ids)

    def test_roster_context_marks_member_agent_creation_permission(self):
        member = User.objects.create_user(username="org-member-roster", email="member-roster@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=self.org,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.logout()
        assert self.client.login(username="org-member-roster", password="pw")
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

        default_resp = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(default_resp.status_code, 200)
        self.assertFalse(default_resp.json()["context"]["canCreateAgents"])
        default_context_resp = self.client.get(reverse("switch_context"))
        self.assertEqual(default_context_resp.status_code, 200)
        default_context_payload = default_context_resp.json()
        self.assertFalse(default_context_payload["context"]["canCreateAgents"])
        organization_option = next(
            option for option in default_context_payload["organizations"] if option["id"] == str(self.org.id)
        )
        self.assertFalse(organization_option["canCreateAgents"])

        self.org.org_settings = {ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS: True}
        self.org.save(update_fields=["org_settings"])

        enabled_resp = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(enabled_resp.status_code, 200)
        self.assertTrue(enabled_resp.json()["context"]["canCreateAgents"])
        enabled_context_resp = self.client.post(
            reverse("switch_context"),
            data=json.dumps({"type": "organization", "id": str(self.org.id)}),
            content_type="application/json",
        )
        self.assertEqual(enabled_context_resp.status_code, 200)
        self.assertTrue(enabled_context_resp.json()["context"]["canCreateAgents"])

    def test_switch_context_for_deleted_agent_returns_org_context(self):
        self.org_agent.soft_delete()
        self._set_personal_context()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.org_agent.id)},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("context", {}).get("type"), "organization")
        self.assertEqual(payload.get("context", {}).get("id"), str(self.org.id))

        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    def test_switch_context_for_org_agent_allows_collaborator_without_membership(self):
        AgentCollaborator.objects.create(agent=self.org_agent, user=self.stranger)

        self.client.logout()
        assert self.client.login(username="stranger", password="pw")
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.stranger.id)
        session["context_name"] = self.stranger.username
        session.save()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.org_agent.id)},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json().get("context", {})
        self.assertEqual(payload.get("type"), "personal")
        self.assertEqual(payload.get("id"), str(self.stranger.id))

    def test_switch_context_for_personal_agent_allows_collaborator(self):
        AgentCollaborator.objects.create(agent=self.personal_agent, user=self.stranger)

        self.client.logout()
        assert self.client.login(username="stranger", password="pw")
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.stranger.id)
        session["context_name"] = self.stranger.username
        session.save()

        resp = self.client.get(
            reverse("switch_context"),
            {"for_agent": str(self.personal_agent.id)},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json().get("context", {})
        self.assertEqual(payload.get("type"), "personal")
        self.assertEqual(payload.get("id"), str(self.stranger.id))

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
    def test_agent_detail_scoping(self):
        self._set_personal_context()
        url = reverse("agent_detail", kwargs={"pk": self.org_agent.id})

        # Direct navigation to a legacy agent page should redirect into the app
        # without mutating the user's persisted session context.
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, f"/app/agents/{self.org_agent.id}/settings")
        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

        # Explicit context override query should be preserved for the app shell.
        resp_with_override = self.client.get(
            url,
            {
                "context_type": "organization",
                "context_id": str(self.org.id),
            },
        )
        self.assertEqual(resp_with_override.status_code, 302)
        redirect = urlsplit(resp_with_override.url)
        self.assertEqual(redirect.path, f"/app/agents/{self.org_agent.id}/settings")
        query = parse_qs(redirect.query)
        self.assertEqual(query.get("context_type"), ["organization"])
        self.assertEqual(query.get("context_id"), [str(self.org.id)])
        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

        self._set_org_context()
        resp2 = self.client.get(url)
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(resp2.url, f"/app/agents/{self.org_agent.id}/settings")

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
    def test_agent_targeted_views_use_agent_owner_context_without_persisting_session(self):
        self._set_personal_context()

        chat_response = self.client.get(
            reverse("agent_chat_shell", kwargs={"pk": self.org_agent.id}),
        )
        self.assertEqual(chat_response.status_code, 302)
        self.assertEqual(chat_response.url, f"/app/agents/{self.org_agent.id}")

        files_response = self.client.get(
            reverse("agent_files", kwargs={"pk": self.org_agent.id}),
        )
        self.assertEqual(files_response.status_code, 302)
        self.assertEqual(files_response.url, f"/app/agents/{self.org_agent.id}/files")

        email_response = self.client.get(
            reverse("agent_email_settings", kwargs={"pk": self.org_agent.id}),
        )
        self.assertEqual(email_response.status_code, 302)
        self.assertEqual(email_response.url, f"/app/agents/{self.org_agent.id}/email")

        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    def test_agent_targeted_apis_allow_authorized_user_outside_current_context(self):
        self._set_personal_context()

        with tempfile.TemporaryDirectory() as tmp_media:
            with override_settings(MEDIA_ROOT=tmp_media, MEDIA_URL="/media/"):
                self.org_agent.avatar.save("avatar.png", ContentFile(b"avatar-bytes"), save=True)

                avatar_response = self.client.get(
                    reverse("agent_avatar", kwargs={"pk": self.org_agent.id}),
                )
                self.assertEqual(avatar_response.status_code, 200)

                files_response = self.client.get(
                    reverse("console_agent_fs_list", kwargs={"agent_id": self.org_agent.id}),
                )
                self.assertEqual(files_response.status_code, 200)

                timeline_response = self.client.get(
                    reverse("console_agent_timeline", kwargs={"agent_id": self.org_agent.id}),
                )
                self.assertEqual(timeline_response.status_code, 200)

                email_settings_response = self.client.get(
                    reverse("console_agent_email_settings", kwargs={"agent_id": self.org_agent.id}),
                )
                self.assertEqual(email_settings_response.status_code, 200)

        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
    def test_org_detail_sets_console_context(self):
        # Visiting org detail should set session context to organization
        url = reverse("organization_detail", kwargs={"org_id": self.org.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        redirect = urlsplit(resp.url)
        self.assertEqual(redirect.path, "/app/team")
        query = parse_qs(redirect.query)
        self.assertEqual(query.get("context_type"), ["organization"])
        self.assertEqual(query.get("context_id"), [str(self.org.id)])
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        self.assertEqual(session.get("context_name"), self.org.name)

    def test_leaving_org_resets_context_to_personal(self):
        # Add a second owner so the original owner can leave
        another = User.objects.create_user(username="other", email="other@example.com", password="pw")
        OrganizationMembership.objects.create(
            org=self.org,
            user=another,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self._set_org_context()
        leave_url = reverse("org_leave_org", kwargs={"org_id": self.org.id})
        resp = self.client.post(leave_url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Verify membership updated
        mem = OrganizationMembership.objects.get(org=self.org, user=self.owner)
        self.assertEqual(mem.status, OrganizationMembership.OrgStatus.REMOVED)
        # Session reset to personal
        session = self.client.session
        self.assertEqual(session.get("context_type"), "personal")
        self.assertEqual(session.get("context_id"), str(self.owner.id))

    def test_header_menu_reflects_context(self):
        # Organization context should show Organization link and hide Profile
        self._set_org_context()
        resp = self.client.get(reverse("console-home"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(str(self.org.id), html)
        self.assertIn("Organization", html)
        # Switch to personal context
        self._set_personal_context()
        resp2 = self.client.get(reverse("console-home"))
        self.assertEqual(resp2.status_code, 200)
        html2 = resp2.content.decode()
        self.assertIn("Profile", html2)

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
    def test_sidebar_nav_reflects_context(self):
        self._set_org_context()
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/app/agents")

        self._set_personal_context()
        resp2 = self.client.get(reverse("agents"))
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(resp2.url, "/app/agents")

    @override_settings(LEGACY_CONSOLE_PAGE_REDIRECTS_ENABLED=True)
    def test_billing_query_switches_to_org_context(self):
        self._set_personal_context()
        billing_url = f"{reverse('billing')}?org_id={self.org.id}"
        resp = self.client.get(billing_url)
        self.assertEqual(resp.status_code, 302)
        redirect = urlsplit(resp.url)
        self.assertEqual(redirect.path, "/app/billing")
        query = parse_qs(redirect.query)
        self.assertEqual(query.get("context_type"), ["organization"])
        self.assertEqual(query.get("context_id"), [str(self.org.id)])
        self.assertEqual(query.get("org_id"), [str(self.org.id)])
        session = self.client.session
        self.assertEqual(session.get('context_type'), 'organization')
        self.assertEqual(session.get('context_id'), str(self.org.id))
        self.assertEqual(session.get('context_name'), self.org.name)

    def test_org_invite_accept_sets_context_and_membership(self):
        # Create invite for a new user
        invitee = User.objects.create_user(username="invitee", email="invitee@example.com", password="pw")
        inv = OrganizationInvite.objects.create(
            org=self.org,
            email=invitee.email,
            role=OrganizationMembership.OrgRole.MEMBER,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )
        # Login as invitee and accept
        self.client.logout()
        assert self.client.login(username="invitee", password="pw")
        url = reverse("org_invite_accept", kwargs={"token": inv.token})
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Context set to org
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        # Membership created/active
        mem = OrganizationMembership.objects.get(org=self.org, user=invitee)
        self.assertEqual(mem.status, OrganizationMembership.OrgStatus.ACTIVE)

    def test_org_invite_reject_sets_context(self):
        invitee = User.objects.create_user(username="invitee2", email="invitee2@example.com", password="pw")
        inv = OrganizationInvite.objects.create(
            org=self.org,
            email=invitee.email,
            role=OrganizationMembership.OrgRole.MEMBER,
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timezone.timedelta(days=7),
            invited_by=self.owner,
        )
        self.client.logout()
        assert self.client.login(username="invitee2", password="pw")
        url = reverse("org_invite_reject", kwargs={"token": inv.token})
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.status_code, 200)
        # Context set to org
        session = self.client.session
        self.assertEqual(session.get("context_type"), "organization")
        self.assertEqual(session.get("context_id"), str(self.org.id))
        # No active membership created by rejection
        self.assertFalse(OrganizationMembership.objects.filter(org=self.org, user=invitee, status=OrganizationMembership.OrgStatus.ACTIVE).exists())
