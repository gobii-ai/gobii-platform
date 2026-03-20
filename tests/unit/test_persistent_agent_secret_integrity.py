from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, tag

from api.domain_validation import DomainPatternValidator
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSecret

User = get_user_model()


@tag("batch_secrets")
class PersistentAgentSecretIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="secret-integrity@example.com",
            email="secret-integrity@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="SecretIntegrityBrowser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="SecretIntegrityAgent",
            charter="Test secret integrity",
            browser_use_agent=self.browser_agent,
        )

    def _create_secret(self, *, key: str, domain: str, value: str, name: str | None = None) -> PersistentAgentSecret:
        secret = PersistentAgentSecret(
            agent=self.agent,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
            domain_pattern=domain,
            name=name or key,
            key=key,
            requested=False,
        )
        secret.set_value(value)
        secret.save()
        return secret

    def test_domain_validator_accepts_subdomain_wildcard_and_rejects_tld_wildcards(self):
        DomainPatternValidator.validate_domain_pattern("*.fidlar.com")
        self.assertEqual(
            DomainPatternValidator.normalize_domain_pattern("*.fidlar.com"),
            "https://*.fidlar.com",
        )

        for pattern in ("*.com", "*.gov", "example.*"):
            with self.assertRaises(ValueError):
                DomainPatternValidator.validate_domain_pattern(pattern)

    def test_model_save_rejects_invalid_domain_without_manual_full_clean(self):
        with self.assertRaises(ValidationError):
            PersistentAgentSecret.objects.create(
                agent=self.agent,
                secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
                domain_pattern="*.gov",
                name="Legacy Secret",
                key="legacy_secret",
                requested=False,
                encrypted_value=b"placeholder",
            )

    def test_audit_command_reports_invalid_persisted_secret_rows(self):
        valid_secret = self._create_secret(
            key="portal_password",
            domain="https://portal.example.com",
            value="portal-secret",
        )
        invalid_secret = self._create_secret(
            key="legacy_password",
            domain="https://legacy.example.com",
            value="legacy-secret",
        )
        PersistentAgentSecret.objects.filter(pk=invalid_secret.pk).update(domain_pattern="*.gov")

        out = StringIO()
        call_command("audit_persistent_agent_secrets", stdout=out)
        output = out.getvalue()

        self.assertIn(str(invalid_secret.id), output)
        self.assertIn(str(self.agent.id), output)
        self.assertIn('"key": "legacy_password"', output)
        self.assertIn('"domain_pattern": "*.gov"', output)
        self.assertIn("Found 1 invalid persistent agent secret(s).", output)
        self.assertNotIn(str(valid_secret.id), output)
