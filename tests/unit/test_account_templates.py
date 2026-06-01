from allauth.account.models import EmailAddress
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse


@tag("batch_pages")
class AccountEmailTemplateTests(TestCase):
    def test_account_email_select_has_hidden_accessible_label(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="account-email-label@example.com",
            email="account-email-label@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=False,
            primary=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account_email"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        select = soup.find("select", {"name": "email"})
        self.assertIsNotNone(select)
        self.assertEqual(select.get("id"), "account-email-action-address")
        label = soup.find("label", {"for": select.get("id")})
        self.assertIsNotNone(label)
        self.assertIn("sr-only", label.get("class", []))
        self.assertEqual(label.get_text(strip=True), "Email address")
