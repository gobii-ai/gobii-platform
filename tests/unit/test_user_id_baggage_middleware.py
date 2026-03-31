from types import SimpleNamespace

from django.db.utils import OperationalError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, tag
from opentelemetry import baggage

from middleware.user_id_baggage import UserIdBaggageMiddleware


class _BrokenUser:
    @property
    def is_authenticated(self):
        raise OperationalError("failed to resolve host 'db'")


@tag("batch_pages")
class UserIdBaggageMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_authenticated_user_sets_baggage_for_request_only(self):
        middleware = UserIdBaggageMiddleware(
            lambda request: HttpResponse(baggage.get_baggage("user.id") or "")
        )
        request = self.factory.get("/")
        request.user = SimpleNamespace(is_authenticated=True, id=123)

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"123")
        self.assertIsNone(baggage.get_baggage("user.id"))

    def test_db_error_during_user_resolution_skips_baggage_instead_of_failing(self):
        middleware = UserIdBaggageMiddleware(lambda request: HttpResponse("ok"))
        request = self.factory.get("/")
        request.user = _BrokenUser()

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
