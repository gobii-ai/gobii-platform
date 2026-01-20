import random
import re

from django.core.exceptions import ValidationError
from django.utils.text import slugify

HANDLE_MIN_LENGTH = 3
HANDLE_MAX_LENGTH = 32
HANDLE_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

RESERVED_HANDLES = frozenset({
    "about",
    "account",
    "accounts",
    "admin",
    "agent",
    "agents",
    "api",
    "app",
    "apps",
    "assets",
    "auth",
    "billing",
    "blog",
    "careers",
    "connect",
    "console",
    "docs",
    "downloads",
    "d",
    "g",
    "health",
    "healthz",
    "help",
    "home",
    "jobs",
    "login",
    "logout",
    "m",
    "media",
    "onboarding",
    "plans",
    "pricing",
    "pretrained-workers",
    "privacy",
    "profile",
    "public",
    "robots",
    "sitemap",
    "signup",
    "solutions",
    "static",
    "status",
    "stripe",
    "support",
    "team",
    "terms",
    "terms-of-service",
    "users",
    "welcome",
})

ADJECTIVES = [
    "bright",
    "calm",
    "clever",
    "curious",
    "daring",
    "gentle",
    "keen",
    "lively",
    "lucid",
    "mighty",
    "nimble",
    "playful",
    "quiet",
    "quick",
    "radar",
    "sharp",
    "steady",
    "swift",
    "vivid",
    "wise",
]

NOUNS = [
    "atlas",
    "beacon",
    "comet",
    "compass",
    "drift",
    "ember",
    "glade",
    "harbor",
    "horizon",
    "isle",
    "kernel",
    "lighthouse",
    "orbit",
    "river",
    "signal",
    "spark",
    "summit",
    "tide",
    "vector",
    "voyage",
]


def normalize_public_handle(value: str) -> str:
    return slugify(value or "")


def is_reserved_handle(handle: str) -> bool:
    return handle in RESERVED_HANDLES


def validate_public_handle(value: str) -> str:
    normalized = normalize_public_handle(value)
    if not normalized:
        raise ValidationError("Handle is required.")
    if len(normalized) < HANDLE_MIN_LENGTH:
        raise ValidationError(f"Handle must be at least {HANDLE_MIN_LENGTH} characters.")
    if len(normalized) > HANDLE_MAX_LENGTH:
        raise ValidationError(f"Handle must be at most {HANDLE_MAX_LENGTH} characters.")
    if not HANDLE_REGEX.match(normalized):
        raise ValidationError("Handle may only include lowercase letters, numbers, and hyphens.")
    if is_reserved_handle(normalized):
        raise ValidationError("That handle is reserved.")
    return normalized


def generate_handle_suggestion() -> str:
    base = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
    if len(base) > HANDLE_MAX_LENGTH:
        base = base[:HANDLE_MAX_LENGTH].rstrip("-")
    return base


def with_handle_suffix(base: str, suffix: int) -> str:
    if suffix <= 1:
        return base
    suffix_text = f"-{suffix}"
    max_base_len = HANDLE_MAX_LENGTH - len(suffix_text)
    trimmed = base[:max_base_len].rstrip("-")
    if not trimmed:
        trimmed = base[:HANDLE_MAX_LENGTH].rstrip("-")
    return f"{trimmed}{suffix_text}"
