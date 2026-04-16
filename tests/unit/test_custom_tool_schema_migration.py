import importlib
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, tag

from api.models import (
    BrowserUseAgent,
    GlobalAgentSkill,
    GlobalAgentSkillCustomTool,
    PersistentAgent,
    PersistentAgentCustomTool,
)


User = get_user_model()


@tag("batch_agent_tools")
class NormalizeCustomToolSchemaTypesMigrationTests(TestCase):
    def setUp(self):
        self.migration = importlib.import_module(
            "api.migrations.0353_normalize_custom_tool_schema_types"
        )
        self.schema_editor = SimpleNamespace(connection=connections["default"])

        self.user = User.objects.create_user(
            username="schema-migration@example.com",
            email="schema-migration@example.com",
            password="pw",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Migration Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Migration Agent",
            charter="Normalize schemas",
            browser_use_agent=self.browser_agent,
        )
        self.global_skill = GlobalAgentSkill.objects.create(
            name="Migration Skill",
            instructions="Use bundled tools.",
        )

    def test_migration_normalizes_persistent_and_global_custom_tool_schemas(self):
        persistent_tool = PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Pohl Searcher",
            tool_name="custom_pohl_searcher",
            description="Search keywords.",
            source_path="/tools/pohl_searcher.py",
            parameters_schema={
                "type": "OBJECT",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
            },
            timeout_seconds=300,
        )
        global_tool = GlobalAgentSkillCustomTool.objects.create(
            global_skill=self.global_skill,
            name="Pipeline Processor",
            tool_name="custom_pipeline_processor",
            description="Process candidates.",
            source_file=SimpleUploadedFile(
                "pipeline_processor.py",
                (
                    b"from _gobii_ctx import main\n\n"
                    b"def run(params, ctx):\n"
                    b"    return {'ok': True}\n\n"
                    b"if __name__ == '__main__':\n"
                    b"    main(run)\n"
                ),
                content_type="text/x-python",
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "candidate_ids": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                    },
                },
            },
            timeout_seconds=300,
        )

        self.migration.normalize_custom_tool_schema_types(apps, self.schema_editor)
        self.migration.normalize_custom_tool_schema_types(apps, self.schema_editor)

        persistent_tool.refresh_from_db()
        global_tool.refresh_from_db()

        self.assertEqual(
            persistent_tool.parameters_schema,
            {
                "type": "object",
                "required": ["keywords"],
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        )
        self.assertEqual(
            global_tool.parameters_schema,
            {
                "type": "object",
                "properties": {
                    "candidate_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [],
            },
        )
