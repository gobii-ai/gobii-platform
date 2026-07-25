import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from asgiref.sync import async_to_sync
from rest_framework.test import APITestCase

from api.agent.tools.spawn_web_task import (
    execute_spawn_web_task,
    get_spawn_web_task_tool,
)
from api.models import (
    ApiKey,
    BrowserSessionTicket,
    BrowserUseAgent,
    BrowserUseAgentTask,
    Organization,
    OrganizationMembership,
    PersistentAgent,
)
from api.services.browser_session_tickets import (
    issue_gobii_browser_task_session,
)
from api.tasks.browser_agent_tasks import (
    _bootstrap_gobii_ui_session,
    _browser_profile_id_for_task,
)


User = get_user_model()


@tag("batch_browser_session_tickets")
@override_settings(
    GOBII_RELEASE_ENV="preview-pr-99",
    PUBLIC_SITE_URL="https://pr-99.ship.gobii.ai",
    SESSION_COOKIE_SECURE=True,
)
class BrowserSessionTicketAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qa-developer@example.com",
            email="qa-developer@example.com",
            password="unused-password",
            is_staff=True,
        )
        self.raw_api_key, self.api_key = ApiKey.create_for_user(
            self.user,
            name="qa-browser",
        )
        self.client.credentials(HTTP_X_API_KEY=self.raw_api_key)

    def _create_ticket(self, **overrides):
        payload = {
            "expected_environment": "preview-pr-99",
            "next_path": "/app/",
            "purpose": "Verify the release candidate UI",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("api:browser-session-ticket-list"),
            data=json.dumps(payload),
            content_type="application/json",
            secure=True,
            HTTP_HOST="pr-99.ship.gobii.ai",
        )

    def _consume(self, login_url, *, host="pr-99.ship.gobii.ai"):
        parsed = urlsplit(login_url)
        raw_token = parsed.fragment.removeprefix("token=")
        return self.client.post(
            parsed.path,
            data={"token": raw_token},
            secure=True,
            HTTP_HOST=host,
        )

    def test_personal_staff_api_key_can_create_browser_session_ticket(self):
        response = self._create_ticket()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            response.json()["login_url"].startswith(
                "https://pr-99.ship.gobii.ai/api/v1/browser-session-tickets/"
            )
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

        ticket = BrowserSessionTicket.objects.get()
        parsed_login_url = urlsplit(response.json()["login_url"])
        raw_token = parsed_login_url.fragment.removeprefix("token=")
        self.assertNotIn(raw_token, parsed_login_url.path)
        self.assertEqual(parsed_login_url.query, "")
        self.assertNotEqual(ticket.token_hash, raw_token)
        self.assertNotIn(raw_token, repr(ticket.__dict__))
        self.assertEqual(ticket.user, self.user)
        self.assertEqual(ticket.api_key, self.api_key)
        self.assertEqual(ticket.source, BrowserSessionTicket.Source.API)
        self.assertEqual(ticket.environment, "preview-pr-99")
        self.assertEqual(ticket.host, "pr-99.ship.gobii.ai")
        self.assertEqual(ticket.purpose, "Verify the release candidate UI")

    def test_ticket_creates_session_then_cannot_be_reused(self):
        create_response = self._create_ticket(next_path="/app/agents/")
        login_url = create_response.json()["login_url"]

        with CaptureQueriesContext(connection) as queries:
            consume_response = self._consume(login_url)

        self.assertEqual(consume_response.status_code, 302)
        self.assertEqual(consume_response["Location"], "/app/agents/")
        self.assertEqual(
            self.client.session["_auth_user_id"],
            str(self.user.id),
        )
        self.assertGreaterEqual(self.client.session.get_expiry_age(), 3500)
        self.assertIsInstance(self.client.session["_session_expiry"], str)
        session_cookie = consume_response.cookies["sessionid"]
        self.assertTrue(session_cookie["secure"])
        self.assertTrue(session_cookie["httponly"])
        self.assertEqual(consume_response["Cache-Control"], "no-store")
        self.assertEqual(consume_response["Referrer-Policy"], "no-referrer")
        ticket_selects = [
            query["sql"]
            for query in queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and "api_browsersessionticket" in query["sql"]
        ]
        self.assertTrue(ticket_selects)
        self.assertNotIn(" JOIN ", ticket_selects[0].upper())

        ticket = BrowserSessionTicket.objects.get()
        self.assertIsNotNone(ticket.consumed_at)
        reuse_response = self._consume(login_url)
        self.assertEqual(reuse_response.status_code, 410)

    def test_landing_page_contains_no_secret_and_does_not_consume_ticket(self):
        create_response = self._create_ticket()
        parsed_login_url = urlsplit(create_response.json()["login_url"])
        raw_token = parsed_login_url.fragment.removeprefix("token=")

        landing_response = self.client.get(
            parsed_login_url.path,
            secure=True,
            HTTP_HOST="pr-99.ship.gobii.ai",
        )

        self.assertEqual(landing_response.status_code, 200)
        self.assertNotIn(raw_token, landing_response.content.decode("utf-8"))
        self.assertIn(
            '"browser-session-form").submit()',
            landing_response.content.decode("utf-8"),
        )
        self.assertIn(
            'window.addEventListener("DOMContentLoaded"',
            landing_response.content.decode("utf-8"),
        )
        self.assertEqual(landing_response["Cache-Control"], "no-store")
        self.assertIsNone(BrowserSessionTicket.objects.get().consumed_at)

    def test_browser_redemption_uses_normal_csrf_protection(self):
        create_response = self._create_ticket()
        parsed_login_url = urlsplit(create_response.json()["login_url"])
        raw_token = parsed_login_url.fragment.removeprefix("token=")
        browser_client = Client(enforce_csrf_checks=True)

        landing_response = browser_client.get(
            parsed_login_url.path,
            secure=True,
            HTTP_HOST="pr-99.ship.gobii.ai",
        )
        csrf_token = browser_client.cookies["csrftoken"].value
        consume_response = browser_client.post(
            parsed_login_url.path,
            data={
                "csrfmiddlewaretoken": csrf_token,
                "token": raw_token,
            },
            secure=True,
            HTTP_HOST="pr-99.ship.gobii.ai",
            HTTP_ORIGIN="https://pr-99.ship.gobii.ai",
        )

        self.assertEqual(landing_response.status_code, 200)
        self.assertEqual(consume_response.status_code, 302)
        self.assertEqual(
            browser_client.session["_auth_user_id"],
            str(self.user.id),
        )

    def test_expired_ticket_is_rejected(self):
        response = self._create_ticket()
        BrowserSessionTicket.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        consume_response = self._consume(response.json()["login_url"])

        self.assertEqual(consume_response.status_code, 410)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_revoked_api_key_invalidates_unconsumed_ticket(self):
        response = self._create_ticket()
        self.api_key.revoke()

        consume_response = self._consume(response.json()["login_url"])

        self.assertEqual(consume_response.status_code, 410)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_invalidates_unconsumed_ticket(self):
        response = self._create_ticket()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        consume_response = self._consume(response.json()["login_url"])

        self.assertEqual(consume_response.status_code, 410)

    def test_environment_must_match_at_issue_and_consume(self):
        mismatch_response = self._create_ticket(
            expected_environment="staging"
        )
        self.assertEqual(mismatch_response.status_code, 400)
        self.assertFalse(BrowserSessionTicket.objects.exists())

        response = self._create_ticket()
        with override_settings(GOBII_RELEASE_ENV="staging"):
            consume_response = self._consume(response.json()["login_url"])
        self.assertEqual(consume_response.status_code, 410)

    def test_ticket_is_bound_to_canonical_host(self):
        response = self._create_ticket()

        consume_response = self._consume(
            response.json()["login_url"],
            host="staging.ship.gobii.ai",
        )

        self.assertEqual(consume_response.status_code, 410)

        create_response = self.client.post(
            reverse("api:browser-session-ticket-list"),
            data=json.dumps({"expected_environment": "preview-pr-99"}),
            content_type="application/json",
            secure=True,
            HTTP_HOST="staging.ship.gobii.ai",
        )
        self.assertEqual(create_response.status_code, 400)

    def test_external_and_ambiguous_redirects_are_rejected(self):
        for unsafe_path in (
            "https://evil.example/steal",
            "//evil.example/steal",
            r"/\evil.example/steal",
            "/app/#secret",
            "/app/\nLocation: https://evil.example",
        ):
            with self.subTest(unsafe_path=unsafe_path):
                response = self._create_ticket(next_path=unsafe_path)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(BrowserSessionTicket.objects.exists())

    def test_non_staff_user_is_rejected(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])

        response = self._create_ticket()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BrowserSessionTicket.objects.exists())

    def test_ticket_creation_is_rate_limited_per_user(self):
        for _index in range(20):
            self.assertEqual(self._create_ticket().status_code, 201)

        response = self._create_ticket()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertEqual(BrowserSessionTicket.objects.count(), 20)

    def test_session_authentication_cannot_mint_ticket(self):
        self.client.credentials()
        self.client.force_login(self.user)

        response = self._create_ticket()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BrowserSessionTicket.objects.exists())

    def test_organization_api_key_cannot_mint_full_user_session(self):
        organization = Organization.objects.create(
            name="QA Organization",
            slug="qa-organization",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        raw_org_key, _api_key = ApiKey.create_for_org(
            organization,
            created_by=self.user,
            name="qa-org",
        )
        self.client.credentials(HTTP_X_API_KEY=raw_org_key)

        response = self._create_ticket()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BrowserSessionTicket.objects.exists())

    @override_settings(
        GOBII_RELEASE_ENV="prod",
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_production_is_hard_disabled(self):
        response = self.client.post(
            reverse("api:browser-session-ticket-list"),
            data=json.dumps({"expected_environment": "prod"}),
            content_type="application/json",
            secure=True,
            HTTP_HOST="gobii.ai",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BrowserSessionTicket.objects.exists())


@tag("batch_browser_session_tickets")
@override_settings(
    GOBII_RELEASE_ENV="preview-pr-99",
    PUBLIC_SITE_URL="https://pr-99.ship.gobii.ai",
)
class GobiiUIBrowserTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="qa-gobii-owner@example.com",
            email="qa-gobii-owner@example.com",
            password="unused-password",
            is_staff=True,
        )
        with patch.object(
            BrowserUseAgent,
            "select_random_proxy",
            return_value=None,
        ):
            self.browser_agent = BrowserUseAgent.objects.create(
                user=self.user,
                name="QA Browser",
            )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="QA Gobii",
            charter="Test preview releases through the Gobii UI.",
            browser_use_agent=self.browser_agent,
        )

    def test_tool_advertises_explicit_gobii_ui_authentication(self):
        schema = get_spawn_web_task_tool(self.agent)

        parameter = schema["function"]["parameters"]["properties"][
            "authenticate_to_gobii_ui"
        ]
        self.assertEqual(parameter["type"], "boolean")
        self.assertIn("credential stays hidden", parameter["description"])

    @patch(
        "api.models.TaskCreditService.check_and_consume_credit_for_owner",
        return_value={"success": True, "credit": None, "error_message": None},
    )
    @patch("api.tasks.browser_agent_tasks.process_browser_use_task.delay")
    def test_staff_owned_gobii_can_create_authenticated_qa_task(
        self,
        mock_delay,
        _mock_consume_credit,
    ):
        result = execute_spawn_web_task(
            self.agent,
            {
                "prompt": "Open the agents page and verify its visible controls.",
                "requires_vision": True,
                "authenticate_to_gobii_ui": True,
            },
        )

        self.assertEqual(result["status"], "pending")
        task = BrowserUseAgentTask.objects.get(id=result["task_id"])
        self.assertTrue(task.authenticate_to_gobii_ui)
        self.assertNotIn("browser-session-tickets", task.prompt)
        self.assertIsNone(_browser_profile_id_for_task(task))
        mock_delay.assert_called_once()

        issued = issue_gobii_browser_task_session(str(task.id))
        self.assertEqual(issued.ticket.source, BrowserSessionTicket.Source.GOBII_BROWSER_TASK)
        self.assertEqual(issued.ticket.browser_task, task)
        self.assertIsNone(issued.ticket.api_key)

        browser_client = Client()
        parsed_login_url = urlsplit(issued.login_url)
        consume_response = browser_client.post(
            parsed_login_url.path,
            data={
                "token": parsed_login_url.fragment.removeprefix("token="),
            },
            secure=True,
            HTTP_HOST="pr-99.ship.gobii.ai",
        )
        self.assertEqual(consume_response.status_code, 302)
        self.assertEqual(
            browser_client.session["_auth_user_id"],
            str(self.user.id),
        )

    def test_non_staff_owned_gobii_is_rejected_before_task_creation(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])

        result = execute_spawn_web_task(
            self.agent,
            {
                "prompt": "Inspect Gobii.",
                "authenticate_to_gobii_ui": True,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("active staff owner", result["message"])
        self.assertFalse(BrowserUseAgentTask.objects.exists())

    @override_settings(
        GOBII_RELEASE_ENV="prod",
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_gobii_ui_authentication_is_rejected_in_production(self):
        result = execute_spawn_web_task(
            self.agent,
            {
                "prompt": "Inspect Gobii.",
                "authenticate_to_gobii_ui": True,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("preview, and staging", result["message"])
        self.assertFalse(BrowserUseAgentTask.objects.exists())

    def test_worker_bootstrap_navigates_before_handoff(self):
        issued = SimpleNamespace(
            login_url=(
                "https://pr-99.ship.gobii.ai/api/v1/"
                "browser-session-tickets/one-time-secret/"
            ),
            ticket=SimpleNamespace(id="ticket-id"),
        )
        browser_session = SimpleNamespace(
            navigate_to=AsyncMock(),
            get_current_page_url=AsyncMock(
                return_value="https://pr-99.ship.gobii.ai/app/agents/"
            ),
        )

        with patch(
            "api.services.browser_session_tickets.issue_gobii_browser_task_session",
            return_value=issued,
        ):
            async_to_sync(_bootstrap_gobii_ui_session)(
                browser_session,
                "browser-task-id",
            )

        browser_session.navigate_to.assert_awaited_once_with(issued.login_url)
        browser_session.get_current_page_url.assert_awaited_once()

    def test_worker_bootstrap_fails_closed_if_login_did_not_reach_app(self):
        issued = SimpleNamespace(
            login_url=(
                "https://pr-99.ship.gobii.ai/api/v1/"
                "browser-session-tickets/one-time-secret/"
            ),
            ticket=SimpleNamespace(id="ticket-id"),
        )
        browser_session = SimpleNamespace(
            navigate_to=AsyncMock(),
            get_current_page_url=AsyncMock(
                return_value="https://pr-99.ship.gobii.ai/accounts/login/"
            ),
        )

        with (
            patch(
                "api.services.browser_session_tickets.issue_gobii_browser_task_session",
                return_value=issued,
            ),
            self.assertRaises(RuntimeError),
        ):
            async_to_sync(_bootstrap_gobii_ui_session)(
                browser_session,
                "browser-task-id",
            )
