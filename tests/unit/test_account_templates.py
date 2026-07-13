from allauth.account.models import EmailAddress
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse


@tag("batch_pages")
class AccountEmailTemplateTests(TestCase):
    def test_account_email_change_input_has_accessible_label(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="account-email-label@example.com",
            email="account-email-label@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account_email"))

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
        email_input = soup.find("input", {"name": "email"})
        self.assertIsNotNone(email_input)
        label = soup.find("label", {"for": email_input.get("id")})
        self.assertIsNotNone(label)
        self.assertIn("Change to", label.get_text(strip=True))
