from urllib.parse import parse_qs

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, tag

from middleware.utm_capture import UTMTrackingMiddleware
from pages.mini_mode import (
    MINI_MODE_COOKIE_MAX_AGE,
    MINI_MODE_COOKIE_NAME,
    MINI_MODE_COOKIE_VALUE,
    clear_mini_mode_cookie,
)
from pages.models import MiniModeCampaignPattern


@tag("batch_pages")
class UTMCaptureMiniModeTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.middleware = UTMTrackingMiddleware(lambda request: HttpResponse("ok"))

    def _build_request(self, path: str):
        request = self.factory.get(path)
        session_middleware = SessionMiddleware(lambda req: HttpResponse("noop"))
        session_middleware.process_request(request)
        request.session.save()
        return request

    def test_exact_campaign_match_sets_cookie(self):
        MiniModeCampaignPattern.objects.create(pattern="agents_202602")

        request = self._build_request("/?utm_campaign=agents_202602")
        response = self.middleware(request)

        self.assertEqual(request.COOKIES.get(MINI_MODE_COOKIE_NAME), MINI_MODE_COOKIE_VALUE)
        self.assertIn(MINI_MODE_COOKIE_NAME, response.cookies)
        cookie = response.cookies[MINI_MODE_COOKIE_NAME]
        self.assertEqual(cookie.value, MINI_MODE_COOKIE_VALUE)
        self.assertEqual(int(cookie["max-age"]), MINI_MODE_COOKIE_MAX_AGE)
        self.assertEqual(cookie["samesite"], "Lax")

    def test_wildcard_campaign_match_sets_cookie(self):
        MiniModeCampaignPattern.objects.create(pattern="c-*")

        request = self._build_request("/?utm_campaign=c-january-launch")
        response = self.middleware(request)

        self.assertIn(MINI_MODE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[MINI_MODE_COOKIE_NAME].value, MINI_MODE_COOKIE_VALUE)

    def test_campaign_match_is_case_insensitive(self):
        MiniModeCampaignPattern.objects.create(pattern="BIGCAMPAIGN")

        request = self._build_request("/?utm_campaign=bigcampaign")
        response = self.middleware(request)

        self.assertIn(MINI_MODE_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[MINI_MODE_COOKIE_NAME].value, MINI_MODE_COOKIE_VALUE)

    def test_inactive_pattern_does_not_set_cookie(self):
        MiniModeCampaignPattern.objects.create(pattern="bigcampaign", is_active=False)

        request = self._build_request("/?utm_campaign=bigcampaign")
        response = self.middleware(request)

        self.assertNotIn(MINI_MODE_COOKIE_NAME, response.cookies)

    def test_non_matching_campaign_does_not_set_cookie(self):
        MiniModeCampaignPattern.objects.create(pattern="bigcampaign")

        request = self._build_request("/?utm_campaign=not-a-match")
        response = self.middleware(request)

        self.assertNotIn(MINI_MODE_COOKIE_NAME, response.cookies)

    def test_clear_cookie_helper_sets_expired_cookie(self):
        request = self._build_request("/")
        response = HttpResponse("ok")

        clear_mini_mode_cookie(response, request)

        self.assertIn(MINI_MODE_COOKIE_NAME, response.cookies)
        cookie = response.cookies[MINI_MODE_COOKIE_NAME]
        self.assertEqual(cookie.value, "")
        self.assertEqual(int(cookie["max-age"]), 0)
        self.assertEqual(cookie["samesite"], "Lax")

    def test_click_ids_first_preserved_when_last_touch_updates(self):
        first_request = self._build_request("/?gclid=first-gclid&msclkid=first-msclkid")
        self.middleware(first_request)

        second_request = self.factory.get("/?gclid=last-gclid&msclkid=last-msclkid")
        second_request.session = first_request.session
        self.middleware(second_request)

        self.assertEqual(
            second_request.session.get("click_ids_first"),
            {"gclid": "first-gclid", "msclkid": "first-msclkid"},
        )
        self.assertEqual(
            second_request.session.get("click_ids_last"),
            {"gclid": "last-gclid", "msclkid": "last-msclkid"},
        )

        querystring = second_request.session.get("utm_querystring", "")
        params = parse_qs(querystring)
        self.assertEqual(params.get("gclid"), ["last-gclid"])
        self.assertEqual(params.get("msclkid"), ["last-msclkid"])
