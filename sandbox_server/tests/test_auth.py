import unittest
from unittest.mock import patch

from sandbox_server.workspace import _require_auth


class SupervisorAuthTests(unittest.TestCase):
    def test_require_auth_fails_closed_when_token_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            error = _require_auth({})

        self.assertEqual(error, {"status": "error", "message": "Sandbox compute API token is not configured."})

    def test_require_auth_accepts_bearer_token(self):
        environ = {"HTTP_AUTHORIZATION": "Bearer sandbox-token"}

        with patch.dict("os.environ", {"SANDBOX_COMPUTE_API_TOKEN": "sandbox-token"}, clear=False):
            error = _require_auth(environ)

        self.assertIsNone(error)

    def test_require_auth_accepts_explicit_supervisor_header(self):
        environ = {"HTTP_X_SANDBOX_COMPUTE_TOKEN": "sandbox-token"}

        with patch.dict("os.environ", {"SANDBOX_COMPUTE_API_TOKEN": "sandbox-token"}, clear=False):
            error = _require_auth(environ)

        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
