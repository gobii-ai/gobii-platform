#!/usr/bin/env python3
"""Fail when a Django app has conflicting leaf migrations."""

import os
import sys
from pathlib import Path

import django
from django.conf import settings
from django.db.migrations.loader import MigrationLoader


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")

django.setup()
# Unit tests disable migrations; this guard only loads their graph and never touches a database.
settings.MIGRATION_MODULES = {}
conflicts = MigrationLoader(None, ignore_no_migrations=True).detect_conflicts()

if conflicts:
    details = "; ".join(
        f"{app}: {', '.join(leaves)}"
        for app, leaves in sorted(conflicts.items())
    )
    raise SystemExit(f"Conflicting migration leaves detected: {details}")

print("Migration graph has one leaf per app.")
