from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, tag

from api.services import sandbox_k8s


@tag("batch_sandbox_k8s")
class SandboxWorkspacePVCTests(SimpleTestCase):
    def _api_exception(self, status_code: int) -> Exception:
        exc = sandbox_k8s.ApiException()
        setattr(exc, "status", status_code)
        return exc

    @patch("api.services.sandbox_k8s._core_v1")
    @patch("api.services.sandbox_k8s.client")
    def test_forbidden_read_attempts_create_and_accepts_conflict(self, client_mock, core_v1_mock):
        core = MagicMock()
        core_v1_mock.return_value = core
        core.read_namespaced_persistent_volume_claim.side_effect = self._api_exception(403)
        core.create_namespaced_persistent_volume_claim.side_effect = self._api_exception(409)

        pvc_name = sandbox_k8s.ensure_workspace_pvc("agent-123", namespace="sandbox")

        self.assertEqual(pvc_name, "sandbox-workspace-agent-123")
        core.create_namespaced_persistent_volume_claim.assert_called_once()

    @patch("api.services.sandbox_k8s._core_v1")
    @patch("api.services.sandbox_k8s.client")
    def test_forbidden_create_raises_sandbox_error(self, client_mock, core_v1_mock):
        core = MagicMock()
        core_v1_mock.return_value = core
        core.read_namespaced_persistent_volume_claim.side_effect = self._api_exception(403)
        core.create_namespaced_persistent_volume_claim.side_effect = self._api_exception(403)

        with self.assertRaises(sandbox_k8s.SandboxK8sError) as context:
            sandbox_k8s.ensure_workspace_pvc("agent-456", namespace="sandbox")

        self.assertIn("persistentvolumeclaims", str(context.exception))

    @patch("api.services.sandbox_k8s._core_v1")
    def test_unexpected_read_error_surfaces_as_sandbox_error(self, core_v1_mock):
        core = MagicMock()
        core_v1_mock.return_value = core
        core.read_namespaced_persistent_volume_claim.side_effect = self._api_exception(500)

        with self.assertRaises(sandbox_k8s.SandboxK8sError):
            sandbox_k8s.ensure_workspace_pvc("agent-789", namespace="sandbox")
