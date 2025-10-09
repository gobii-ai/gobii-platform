from urllib.parse import parse_qs, urlparse

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

from config.socialaccount_adapter import GobiiSocialAccountAdapter


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
