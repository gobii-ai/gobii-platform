from urllib.parse import parse_qs, urlparse
from types import SimpleNamespace

from allauth.core import context
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, tag
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import signing

from config.socialaccount_adapter import (
    GobiiSocialAccountAdapter,
    OAUTH_ATTRIBUTION_COOKIE,
    OAUTH_CHARTER_COOKIE,
)


@tag("batch_email")
class GoogleSocialAccountTests(TestCase):
    def setUp(self) -> None:
        site = Site.objects.get_current()
        self.app = SocialApp.objects.create(
            provider="google",
            name="google",
            client_id="dummy-client",
            secret="dummy-secret",
        )
        self.app.sites.add(site)

    def test_login_flow_includes_select_account_prompt(self) -> None:
        response = self.client.get(reverse("google_login"))

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        query = parse_qs(parsed.query)

        self.assertIn("accounts.google.com", parsed.netloc)
        self.assertEqual(query.get("prompt"), ["select_account"])

    def test_existing_email_blocks_social_login(self) -> None:
        user_model = get_user_model()
        user_model.objects.create_user(
            username="existing-user",
            email="existing@example.com",
            password="dummy-pass",
        )

        request = RequestFactory().get(reverse("google_login"))
        request.user = AnonymousUser()

        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()

        storage = FallbackStorage(request)
        setattr(request, "_messages", storage)

        provider = self.app.get_provider(request)
        sociallogin = provider.sociallogin_from_response(
            request,
            {
                "sub": "1234567890",
                "email": "existing@example.com",
                "email_verified": True,
                "given_name": "Existing",
                "family_name": "User",
            },
        )

        context.request = request
        self.addCleanup(lambda: setattr(context, "request", None))

        sociallogin.lookup()

        adapter = GobiiSocialAccountAdapter(request)
        with self.assertRaises(ImmediateHttpResponse) as exc:
            adapter.pre_social_login(request, sociallogin)

        response = exc.exception.response
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("account_login"))

        rendered_messages = list(storage)
        self.assertTrue(
            any("already have an account" in msg.message for msg in rendered_messages),
            "Expected a helpful error message instructing the user to sign in via email/password.",
        )

    def test_pre_social_login_restores_attribution_keys_from_cookie(self) -> None:
        request = RequestFactory().get(reverse("google_login"))
        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()

        user_model = get_user_model()
        request.user = user_model.objects.create_user(
            username="signed-in-oauth-user",
            email="signed-in-oauth-user@example.com",
            password="dummy-pass",
        )

        stashed = {
            "utm_first_touch": {"utm_source": "meta", "utm_medium": "paid_social"},
            "utm_last_touch": {"utm_source": "meta", "utm_campaign": "retargeting"},
            "click_ids_first": {"gclid": "first-gclid"},
            "click_ids_last": {"gclid": "last-gclid"},
            "fbclid_first": "first-fbclid",
            "fbclid_last": "last-fbclid",
            "utm_querystring": "utm_source=meta&utm_campaign=retargeting&fbclid=last-fbclid",
        }
        request.COOKIES[OAUTH_ATTRIBUTION_COOKIE] = signing.dumps(stashed, compress=True)

        sociallogin = SimpleNamespace(
            account=SimpleNamespace(pk=True),
            user=SimpleNamespace(email="signed-in-oauth-user@example.com"),
        )
        adapter = GobiiSocialAccountAdapter(request)
        adapter.pre_social_login(request, sociallogin)

        self.assertEqual(request.session.get("utm_first_touch"), stashed["utm_first_touch"])
        self.assertEqual(request.session.get("utm_last_touch"), stashed["utm_last_touch"])
        self.assertEqual(request.session.get("click_ids_first"), stashed["click_ids_first"])
        self.assertEqual(request.session.get("click_ids_last"), stashed["click_ids_last"])
        self.assertEqual(request.session.get("fbclid_first"), stashed["fbclid_first"])
        self.assertEqual(request.session.get("fbclid_last"), stashed["fbclid_last"])
        self.assertEqual(request.session.get("utm_querystring"), stashed["utm_querystring"])

    def test_pre_social_login_restores_charter_keys_from_cookie(self) -> None:
        request = RequestFactory().get(reverse("google_login"))
        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session.save()

        user_model = get_user_model()
        request.user = user_model.objects.create_user(
            username="signed-in-charter-oauth-user",
            email="signed-in-charter-oauth-user@example.com",
            password="dummy-pass",
        )

        stashed = {
            "agent_charter": "Cookie charter",
            "agent_charter_source": "template",
            "agent_charter_override": "override charter",
        }
        request.COOKIES[OAUTH_CHARTER_COOKIE] = signing.dumps(stashed, compress=True)

        sociallogin = SimpleNamespace(
            account=SimpleNamespace(pk=True),
            user=SimpleNamespace(email="signed-in-charter-oauth-user@example.com"),
        )
        adapter = GobiiSocialAccountAdapter(request)
        adapter.pre_social_login(request, sociallogin)

        self.assertEqual(request.session.get("agent_charter"), "Cookie charter")
        self.assertEqual(request.session.get("agent_charter_source"), "template")
        self.assertEqual(request.session.get("agent_charter_override"), "override charter")

    def test_pre_social_login_does_not_overwrite_existing_attribution_keys(self) -> None:
        request = RequestFactory().get(reverse("google_login"))
        session_middleware = SessionMiddleware(lambda req: None)
        session_middleware.process_request(request)
        request.session["utm_last_touch"] = {"utm_source": "existing-source"}
        request.session.save()

        user_model = get_user_model()
        request.user = user_model.objects.create_user(
            username="signed-in-existing-oauth-user",
            email="signed-in-existing-oauth-user@example.com",
            password="dummy-pass",
        )

        stashed = {
            "utm_last_touch": {"utm_source": "cookie-source"},
        }
        request.COOKIES[OAUTH_ATTRIBUTION_COOKIE] = signing.dumps(stashed, compress=True)

        sociallogin = SimpleNamespace(
            account=SimpleNamespace(pk=True),
            user=SimpleNamespace(email="signed-in-existing-oauth-user@example.com"),
        )
        adapter = GobiiSocialAccountAdapter(request)
        adapter.pre_social_login(request, sociallogin)

        self.assertEqual(request.session.get("utm_last_touch"), {"utm_source": "existing-source"})
