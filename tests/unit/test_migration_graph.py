from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, override_settings, tag


@tag("batch_pages")
class MigrationGraphTests(SimpleTestCase):
    @override_settings(MIGRATION_MODULES={})
    def test_migration_graph_has_no_conflicting_leaves(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)

        self.assertEqual(loader.detect_conflicts(), {})
