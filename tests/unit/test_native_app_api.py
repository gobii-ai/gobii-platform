import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse

from api.agent.files.filespace_service import write_bytes_to_dir
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    NativeAppSession,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
    build_web_agent_address,
    build_web_user_address,
)
from app_api.auth import create_native_app_session


CHANNEL_LAYER_SETTINGS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}


@override_settings(
    CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
@tag("batch_agent_chat")
class NativeAppAuthAPITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_model = get_user_model()

    def test_sign_up_returns_tokens_and_creates_native_session(self):
        with patch("app_api.views.Analytics.track_event") as mock_track_event:
            response = self.client.post(
                "/api/app/v1/auth/sign-up/",
                data=json.dumps(
                    {
                        "email": "native-signup@example.com",
                        "password": "password123",
                        "passwordConfirmation": "password123",
                        "deviceName": "Matt iPhone",
                        "devicePlatform": "ios",
                        "appVersion": "1.0.0",
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()

        user = self.user_model.objects.get(email="native-signup@example.com")
        email_address = EmailAddress.objects.get(user=user, email="native-signup@example.com")
        session = NativeAppSession.objects.get(user=user)

        self.assertEqual(payload["user"]["email"], "native-signup@example.com")
        self.assertEqual(payload["tokens"]["accessToken"].split(".", 1)[0], str(session.id))
        self.assertEqual(payload["tokens"]["refreshToken"].split(".", 1)[0], str(session.id))
        self.assertFalse(payload["emailVerification"]["isVerified"])
        self.assertEqual(session.device_name, "Matt iPhone")
        self.assertEqual(session.device_platform, "ios")
        self.assertEqual(session.app_version, "1.0.0")
        self.assertTrue(email_address.primary)

        self.assertTrue(
            any(
                call.kwargs.get("source") == "App"
                and call.kwargs.get("event") == "Log In"
                for call in mock_track_event.call_args_list
            )
        )

    @override_settings(GOBII_EMAIL_DOMAIN_BLOCKLIST={"blocked.test"})
    @patch("config.allauth_adapter.is_disposable_domain", return_value=False)
    def test_sign_up_applies_allauth_email_blocking_rules(self, _mock_is_disposable):
        response = self.client.post(
            "/api/app/v1/auth/sign-up/",
            data=json.dumps(
                {
                    "email": "user@blocked.test",
                    "password": "password123",
                    "passwordConfirmation": "password123",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["errorCode"], "SIGNUP_VALIDATION_ERROR")
        self.assertIn("unable to create an account", payload["error"].lower())

    @override_settings(ACCOUNT_ALLOW_PASSWORD_LOGIN=False)
    def test_sign_in_respects_password_login_toggle(self):
        response = self.client.post(
            "/api/app/v1/auth/sign-in/",
            data=json.dumps({"email": "nobody@example.com", "password": "password123"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["errorCode"], "PASSWORD_LOGIN_DISABLED")

    @override_settings(FIRST_RUN_SETUP_ENABLED=True)
    @patch("setup.middleware.is_initial_setup_complete", return_value=False)
    def test_sign_in_bypasses_first_run_setup_redirect(self, _mock_setup_incomplete):
        user = self.user_model.objects.create_user(
            username="native-setup-login",
            email="native-setup-login@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        )

        with patch("app_api.views.Analytics.track_event"):
            response = self.client.post(
                "/api/app/v1/auth/sign-in/",
                data=json.dumps({"email": user.email, "password": "password123"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("tokens", response.json())

    def test_sign_in_refresh_and_logout_rotate_and_revoke_tokens(self):
        user = self.user_model.objects.create_user(
            username="native-login",
            email="native-login@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        )

        with patch("app_api.views.Analytics.track_event"):
            sign_in_response = self.client.post(
                "/api/app/v1/auth/sign-in/",
                data=json.dumps(
                    {
                        "email": user.email,
                        "password": "password123",
                        "deviceName": "Matt MacBook",
                        "devicePlatform": "macos",
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(sign_in_response.status_code, 200, sign_in_response.content)
        sign_in_payload = sign_in_response.json()
        access_token = sign_in_payload["tokens"]["accessToken"]
        refresh_token = sign_in_payload["tokens"]["refreshToken"]

        me_response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(me_response.status_code, 200)

        refresh_response = self.client.post(
            "/api/app/v1/auth/refresh/",
            data=json.dumps({"refreshToken": refresh_token, "appVersion": "1.0.1"}),
            content_type="application/json",
        )
        self.assertEqual(refresh_response.status_code, 200, refresh_response.content)
        refresh_payload = refresh_response.json()
        rotated_access_token = refresh_payload["tokens"]["accessToken"]
        rotated_refresh_token = refresh_payload["tokens"]["refreshToken"]

        stale_access_response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(stale_access_response.status_code, 401)

        stale_refresh_response = self.client.post(
            "/api/app/v1/auth/refresh/",
            data=json.dumps({"refreshToken": refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(stale_refresh_response.status_code, 401)

        rotated_me_response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {rotated_access_token}",
        )
        self.assertEqual(rotated_me_response.status_code, 200)

        with patch("app_api.views.Analytics.track_event"):
            logout_response = self.client.post(
                "/api/app/v1/auth/logout/",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {rotated_access_token}",
            )
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(logout_response.json(), {"revoked": True})

        revoked_access_response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {rotated_access_token}",
        )
        self.assertEqual(revoked_access_response.status_code, 401)

        revoked_refresh_response = self.client.post(
            "/api/app/v1/auth/refresh/",
            data=json.dumps({"refreshToken": rotated_refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(revoked_refresh_response.status_code, 401)

        session = NativeAppSession.objects.get(user=user)
        self.assertIsNotNone(session.revoked_at)
        self.assertEqual(session.device_name, "Matt MacBook")
        self.assertEqual(session.device_platform, "macos")
        self.assertEqual(session.app_version, "1.0.1")

    def test_resend_verification_reuses_primary_email(self):
        user = self.user_model.objects.create_user(
            username="native-verify",
            email="native-verify@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=False,
            primary=True,
        )
        credentials = create_native_app_session(user)

        with patch.object(EmailAddress, "send_confirmation") as mock_send_confirmation:
            response = self.client.post(
                "/api/app/v1/auth/email/resend-verification/",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verified"], False)
        mock_send_confirmation.assert_called_once()


@override_settings(
    CHANNEL_LAYERS=CHANNEL_LAYER_SETTINGS,
    PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=False,
)
@tag("batch_agent_chat")
class NativeAppAgentAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="native-agent-owner",
            email="native-agent-owner@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=cls.user,
            email=cls.user.email,
            verified=True,
            primary=True,
        )

        cls.personal_browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Personal Browser Agent",
        )
        cls.personal_agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Personal Native Agent",
            charter="Handle personal native app chat.",
            browser_use_agent=cls.personal_browser_agent,
        )
        cls._seed_web_conversation(cls.personal_agent, cls.user, "Personal hello")

        cls.organization = Organization.objects.create(
            name="Native Org",
            slug="native-org",
            plan="free",
            created_by=cls.user,
        )
        billing = cls.organization.billing
        billing.purchased_seats = 3
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=cls.organization,
            user=cls.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        cls.org_browser_agent = BrowserUseAgent.objects.create(
            user=cls.user,
            name="Org Browser Agent",
        )
        cls.org_agent = PersistentAgent.objects.create(
            user=cls.user,
            organization=cls.organization,
            name="Org Native Agent",
            charter="Handle org native app chat.",
            browser_use_agent=cls.org_browser_agent,
        )
        cls._seed_web_conversation(cls.org_agent, cls.user, "Org hello")

    @classmethod
    def _seed_web_conversation(cls, agent, user, body):
        user_address = build_web_user_address(user.id, agent.id)
        agent_address = build_web_agent_address(agent.id)
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=agent_address,
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=user_address,
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            conversation=conversation,
            owner_agent=agent,
            body=body,
            raw_payload={"source": "test"},
        )

    def setUp(self):
        self.client = Client()
        self.credentials = create_native_app_session(self.user)

    def _auth_headers(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.credentials.access_token}"}

    def test_me_defaults_to_personal_context_and_accepts_org_override(self):
        personal_response = self.client.get(
            "/api/app/v1/me/",
            **self._auth_headers(),
        )
        self.assertEqual(personal_response.status_code, 200)
        personal_payload = personal_response.json()
        self.assertEqual(personal_payload["currentContext"]["type"], "personal")
        self.assertEqual(personal_payload["currentContext"]["id"], str(self.user.id))

        org_response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.organization.id),
            HTTP_X_GOBII_TIMEZONE="America/Chicago",
        )
        self.assertEqual(org_response.status_code, 200)
        org_payload = org_response.json()
        self.assertEqual(org_payload["currentContext"]["type"], "organization")
        self.assertEqual(org_payload["currentContext"]["id"], str(self.organization.id))
        self.assertEqual(org_payload["timezone"], "America/Chicago")
        self.assertEqual(
            {context["type"] for context in org_payload["availableContexts"]},
            {"personal", "organization"},
        )

    def test_invalid_org_context_is_rejected(self):
        response = self.client.get(
            "/api/app/v1/me/",
            HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID="00000000-0000-0000-0000-000000000000",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["errorCode"], "INVALID_CONTEXT")

    def test_roster_filters_agents_by_selected_context(self):
        personal_response = self.client.get(
            "/api/app/v1/agents/",
            **self._auth_headers(),
        )
        self.assertEqual(personal_response.status_code, 200)
        personal_ids = {agent["id"] for agent in personal_response.json()["agents"]}
        self.assertIn(str(self.personal_agent.id), personal_ids)
        self.assertNotIn(str(self.org_agent.id), personal_ids)

        org_response = self.client.get(
            "/api/app/v1/agents/",
            HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.organization.id),
        )
        self.assertEqual(org_response.status_code, 200)
        org_payload = org_response.json()
        org_ids = {agent["id"] for agent in org_payload["agents"]}
        self.assertIn(str(self.org_agent.id), org_ids)
        self.assertNotIn(str(self.personal_agent.id), org_ids)
        self.assertEqual(org_payload["currentContext"]["id"], str(self.organization.id))

    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_agent_create_uses_stateless_context_headers(self, _mock_delay):
        with self.captureOnCommitCallbacks(execute=True), patch("app_api.views.Analytics.track_event"):
            response = self.client.post(
                "/api/app/v1/agents/",
                data=json.dumps({"message": "Create an org-native assistant"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
                HTTP_X_GOBII_CONTEXT_TYPE="organization",
                HTTP_X_GOBII_CONTEXT_ID=str(self.organization.id),
            )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        created_agent = PersistentAgent.objects.get(id=payload["agentId"])
        self.assertEqual(created_agent.organization_id, self.organization.id)

    def test_timeline_uses_app_file_routes_for_preview_urls(self):
        step = PersistentAgentStep.objects.create(
            agent=self.personal_agent,
            description="Create hero image",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="create_image",
            tool_params={
                "prompt": "Minimal poster art",
                "file_path": "/exports/generated-image.png",
            },
            result=json.dumps(
                {
                    "status": "ok",
                    "file": "$[/exports/generated-image.png]",
                }
            ),
        )

        response = self.client.get(
            f"/api/app/v1/agents/{self.personal_agent.id}/timeline/",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        entries = [
            entry
            for event in payload.get("events", [])
            if event.get("kind") == "steps"
            for entry in event.get("entries", [])
        ]
        image_entry = next(entry for entry in entries if entry.get("toolName") == "create_image")
        preview_url = image_entry["createImageUrl"]
        parsed = urlparse(preview_url)

        self.assertNotIn("/console/api/", preview_url)
        self.assertEqual(
            parsed.path,
            reverse("app_api:agent_fs_download", kwargs={"agent_id": self.personal_agent.id}),
        )
        self.assertEqual(parse_qs(parsed.query)["path"], ["/exports/generated-image.png"])

    @patch("api.agent.tasks.process_agent_events_task.delay")
    def test_message_post_creates_message_event(self, _mock_delay):
        with self.captureOnCommitCallbacks(execute=True), patch("app_api.views.Analytics.track_event"):
            response = self.client.post(
                f"/api/app/v1/agents/{self.personal_agent.id}/messages/",
                data=json.dumps({"body": "Run the weekly summary"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["event"]["kind"], "message")
        self.assertEqual(payload["event"]["message"]["bodyText"], "Run the weekly summary")
        stored = PersistentAgentMessage.objects.filter(
            owner_agent=self.personal_agent,
            body="Run the weekly summary",
        ).latest("timestamp")
        self.assertEqual(stored.from_endpoint.address, build_web_user_address(self.user.id, self.personal_agent.id))

    def test_file_download_endpoint_uses_bearer_auth(self):
        result = write_bytes_to_dir(
            self.personal_agent,
            b"native export",
            "/exports/native-export.txt",
            "text/plain",
        )
        self.assertEqual(result["status"], "ok")

        response = self.client.get(
            f"/api/app/v1/agents/{self.personal_agent.id}/files/download/",
            data={"path": "/exports/native-export.txt"},
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"native export")

    def test_session_lifecycle_endpoints_reuse_web_session_payload_shape(self):
        with patch("app_api.views.Analytics.track_event"):
            start_response = self.client.post(
                f"/api/app/v1/agents/{self.personal_agent.id}/sessions/start/",
                data=json.dumps({"ttl_seconds": 45, "isVisible": False}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            )

        self.assertEqual(start_response.status_code, 200, start_response.content)
        start_payload = start_response.json()
        self.assertFalse(start_payload["is_visible"])
        self.assertEqual(start_payload["ttl_seconds"], 45)

        heartbeat_response = self.client.post(
            f"/api/app/v1/agents/{self.personal_agent.id}/sessions/heartbeat/",
            data=json.dumps(
                {
                    "session_key": start_payload["session_key"],
                    "ttl_seconds": 45,
                    "isVisible": True,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
        )
        self.assertEqual(heartbeat_response.status_code, 200, heartbeat_response.content)
        self.assertTrue(heartbeat_response.json()["is_visible"])

        with patch("app_api.views.Analytics.track_event"):
            end_response = self.client.post(
                f"/api/app/v1/agents/{self.personal_agent.id}/sessions/end/",
                data=json.dumps({"session_key": start_payload["session_key"]}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.credentials.access_token}",
            )
        self.assertEqual(end_response.status_code, 200, end_response.content)
        self.assertIn("ended_at", end_response.json())
