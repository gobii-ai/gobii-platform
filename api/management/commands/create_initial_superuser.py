import os
import secrets
import string
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create an initial superuser if none exists. Password is generated if not provided."

    def handle(self, *args, **options):
        User = get_user_model()

        # If a superuser already exists, do nothing
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Superuser already exists; skipping initial creation.")
            return

        email = os.environ.get("INITIAL_SUPERUSER_EMAIL", "admin@example.com").strip() or "admin@example.com"
        username_env = os.environ.get("INITIAL_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("INITIAL_SUPERUSER_PASSWORD")

        if not password:
            # Generate a strong random password
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
            password = "".join(secrets.choice(alphabet) for _ in range(24))

        # Determine username field dynamically
        username_field = getattr(User, "USERNAME_FIELD", "username")
        if username_field == "email":
            username_value = email
        else:
            username_value = username_env or (email.split("@")[0] if "@" in email else "admin")

        create_kwargs = {username_field: username_value, "email": email}
        user = User.objects.create_superuser(**create_kwargs, password=password)

        self.stdout.write(self.style.SUCCESS("Initial superuser created."))
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Save these credentials somewhere secure:"))
        self.stdout.write(self.style.HTTP_INFO(f"  Email: {email}"))
        self.stdout.write(self.style.WARNING(f"  Password: {password}"))
        self.stdout.write("")
        self.stdout.write("To change later: use Django admin or `python manage.py changepassword <user>`")
