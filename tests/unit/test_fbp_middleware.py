from unittest import mock

from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, tag

from middleware.fbp_middleware import FbpMiddleware, get_or_make_fbp


@tag("batch_fbp_middleware")
class FbpMiddlewareTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.middleware = FbpMiddleware(lambda request: HttpResponse("ok"))

    def _build_request(self, cookies=None):
        request = self.factory.get("/")
        session_middleware = SessionMiddleware(lambda req: HttpResponse("noop"))
        session_middleware.process_request(request)
        request.session.save()
        if cookies:
            request.COOKIES.update(cookies)
        return request

    @tag("batch_fbp_middleware")
    def test_get_or_make_generates_and_persists_identifier(self):
        request = self._build_request()
        fixed_time = 1_720_000_000.123
        random_value = 9876543210

        with mock.patch("middleware.fbp_middleware.time.time", return_value=fixed_time), mock.patch(
            "middleware.fbp_middleware.random.randint", return_value=random_value
        ):
            fbp = get_or_make_fbp(request)

        expected = f"fb.1.{int(fixed_time * 1000)}.{random_value}"
        self.assertEqual(fbp, expected)
        self.assertEqual(request.session[settings.FBP_COOKIE_NAME], expected)

    @tag("batch_fbp_middleware")
    def test_middleware_sets_cookie_when_missing(self):
        request = self._build_request()
        fixed_time = 1_720_000_001.456
        random_value = 1122334455
        expected = f"fb.1.{int(fixed_time * 1000)}.{random_value}"

        with mock.patch("middleware.fbp_middleware.time.time", return_value=fixed_time), mock.patch(
            "middleware.fbp_middleware.random.randint", return_value=random_value
        ):
            response = self.middleware(request)

        cookie = response.cookies[settings.FBP_COOKIE_NAME]
        self.assertEqual(cookie.value, expected)
        self.assertEqual(int(cookie["max-age"]), settings.FBP_MAX_AGE)
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertFalse(bool(cookie["httponly"]))

    @tag("batch_fbp_middleware")
    def test_middleware_respects_existing_cookie(self):
        cookie_value = "fb.1.1000.2000"
        request = self._build_request({settings.FBP_COOKIE_NAME: cookie_value})

        response = self.middleware(request)

        self.assertNotIn(settings.FBP_COOKIE_NAME, request.session)
        self.assertNotIn(settings.FBP_COOKIE_NAME, response.cookies)
