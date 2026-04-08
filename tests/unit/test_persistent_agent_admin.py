import json
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.test import Client, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from api.admin import PersistentAgentAdmin
from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    GlobalAgentSkill,
    GlobalAgentSkillCustomTool,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    PersistentAgentSkill,
    PersistentAgentSystemMessage,
    PersistentAgentSystemMessageBroadcast,
)
from util.analytics import AnalyticsEvent, AnalyticsSource


@tag("batch_api_persistent_agents")
class PersistentAgentAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.request_factory = RequestFactory()
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.admin_user)

        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.admin_user,
            name="Browser Agent",
        )

        self.persistent_agent = PersistentAgent.objects.create(
            user=self.admin_user,
            name="Persistent Agent",
            charter="Assist with tasks",
            browser_use_agent=self.browser_agent,
        )

    def _create_agent(self, **overrides):
        """Helper to create additional persistent agents with unique browser agents."""
        browser_agent = BrowserUseAgent.objects.create(
            user=overrides.get("user", self.admin_user),
            name=overrides.pop("browser_name", f"Browser Agent {BrowserUseAgent.objects.count()}"),
        )
        defaults = {
            "user": self.admin_user,
            "name": "Persistent Agent Extra",
            "charter": "Assist with tasks",
            "browser_use_agent": browser_agent,
        }
        defaults.update(overrides)
        return PersistentAgent.objects.create(**defaults)

    def _create_global_skill_with_custom_tool(self, *, name="weather-check", is_active=True):
        skill = GlobalAgentSkill.objects.create(
            name=name,
            description="Check weather",
            tools=["weather"],
            secrets=[
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key for weather lookups.",
                }
            ],
            instructions="Check weather and summarize it.",
            is_active=is_active,
        )
        tool = GlobalAgentSkillCustomTool(
            global_skill=skill,
            name="Weather Tool",
            tool_name="weather_tool",
            description="Reads weather data.",
            parameters_schema={"type": "object", "properties": {}, "required": []},
            timeout_seconds=120,
        )
        tool.source_file.save(
            "weather_tool.py",
            ContentFile(
                (
                    b"from _gobii_ctx import main\n\n"
                    b"def run(params, ctx):\n"
                    b"    return {'ok': True}\n\n"
                    b"if __name__ == '__main__':\n"
                    b"    main(run)\n"
                )
            ),
            save=False,
        )
        tool.full_clean()
        tool.save()
        return skill

    def test_trigger_processing_queues_valid_ids(self):
        url = reverse("admin:api_persistentagent_trigger_processing")
        invalid_id = "not-a-uuid"
        submitted_ids = f"{self.persistent_agent.id}\n{invalid_id}\n{self.persistent_agent.id}"

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"agent_ids": submitted_ids}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(self.persistent_agent.id))

        messages = list(response.context["messages"])
        self.assertTrue(any("Queued event processing for 1 persistent agent" in message.message for message in messages))
        self.assertTrue(any("Skipped invalid ID(s)" in message.message for message in messages))

    def test_trigger_processing_page_renders_form(self):
        url = reverse("admin:api_persistentagent_trigger_processing")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trigger Event Processing")
        self.assertContains(response, "Persistent Agent IDs")
        self.assertContains(response, "Inactive agents are always skipped.")
        self.assertNotContains(response, 'id="id_only_active"')
        self.assertContains(response, 'id="id_only_with_user" value="1" checked')
        self.assertNotContains(response, 'id="id_skip_expired" value="1" checked')

    def test_trigger_processing_skips_inactive_agents_by_default(self):
        inactive_agent = self._create_agent(is_active=False, name="Inactive Agent")
        url = reverse("admin:api_persistentagent_trigger_processing")
        submitted_ids = f"{inactive_agent.id}\n{self.persistent_agent.id}"

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": submitted_ids,
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(self.persistent_agent.id))
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped inactive agent ID(s)" in message.message for message in messages))

    def test_trigger_processing_skips_inactive_agents_even_when_checkbox_off(self):
        inactive_agent = self._create_agent(is_active=False, name="Inactive Agent")
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(inactive_agent.id),
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped inactive agent ID(s)" in message.message for message in messages))

    def test_persistent_agent_admin_includes_skill_inline(self):
        model_admin = admin.site._registry[PersistentAgent]
        inline_models = {inline.model for inline in model_admin.inlines}

        self.assertIn(PersistentAgentSkill, inline_models)
        skill_inline = next(inline for inline in model_admin.inlines if inline.model is PersistentAgentSkill)
        self.assertIn("global_skill", skill_inline.fields)
        self.assertIn("secrets", skill_inline.fields)
        self.assertIn("global_skill", skill_inline.readonly_fields)

    def test_persistent_agent_skill_admin_is_registered(self):
        self.assertIn(PersistentAgentSkill, admin.site._registry)

    def test_global_agent_skill_admin_is_registered(self):
        self.assertIn(GlobalAgentSkill, admin.site._registry)
        admin_view = admin.site._registry[GlobalAgentSkill]
        self.assertIn("is_active", admin_view.list_display)
        self.assertIn("instructions", admin_view.search_fields)
        self.assertIn("secrets", admin_view.fields)
        inline_models = {inline.model for inline in admin_view.inlines}
        self.assertIn(GlobalAgentSkillCustomTool, inline_models)

    def test_global_agent_skill_changelist_shows_import_json_link(self):
        response = self.client.get(reverse("admin:api_globalagentskill_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import Skill JSON")
        self.assertContains(response, reverse("admin:api_globalagentskill_import_json"))

    def test_global_agent_skill_change_form_shows_export_json_link(self):
        skill = self._create_global_skill_with_custom_tool()

        response = self.client.get(reverse("admin:api_globalagentskill_change", args=[skill.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Export JSON")
        self.assertContains(response, reverse("admin:api_globalagentskill_export_json", args=[skill.pk]))

    def test_global_agent_skill_export_json_returns_content_only_payload(self):
        skill = self._create_global_skill_with_custom_tool(name="Check Weather Skill")

        response = self.client.get(reverse("admin:api_globalagentskill_export_json", args=[skill.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn('attachment; filename="Check_Weather_Skill.json"', response["Content-Disposition"])

        payload = json.loads(response.content)
        self.assertEqual(payload["name"], "Check Weather Skill")
        self.assertEqual(payload["description"], "Check weather")
        self.assertEqual(payload["tools"], ["weather"])
        self.assertEqual(
            payload["secrets"],
            [
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key for weather lookups.",
                }
            ],
        )
        self.assertEqual(payload["instructions"], "Check weather and summarize it.")
        self.assertEqual(len(payload["custom_tools"]), 1)
        self.assertEqual(payload["custom_tools"][0]["tool_name"], "custom_weather_tool")
        self.assertIn("def run(params, ctx):", payload["custom_tools"][0]["source_code"])
        self.assertNotIn("id", payload)
        self.assertNotIn("created_at", payload)
        self.assertNotIn("updated_at", payload)
        self.assertNotIn("is_active", payload)

    def test_global_agent_skill_import_json_creates_skill_and_bundled_tools(self):
        payload = {
            "name": "check-weather",
            "description": "Check weather and summarize impact.",
            "tools": ["weather"],
            "secrets": [
                {
                    "name": "Weather API key",
                    "key": "WEATHER_API_KEY",
                    "secret_type": "env_var",
                    "description": "API key for extended weather lookups.",
                }
            ],
            "instructions": "Use this skill for weather tasks.",
            "custom_tools": [
                {
                    "name": "Weather Briefing",
                    "tool_name": "weather_briefing",
                    "description": "Prepare a weather briefing.",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                        },
                        "required": ["location"],
                    },
                    "timeout_seconds": 120,
                    "source_code": (
                        "from _gobii_ctx import main\n\n"
                        "def run(params, ctx):\n"
                        "    return {'location': params['location']}\n\n"
                        "if __name__ == '__main__':\n"
                        "    main(run)\n"
                    ),
                }
            ],
        }

        response = self.client.post(
            reverse("admin:api_globalagentskill_import_json"),
            data={
                "json_file": SimpleUploadedFile(
                    "skill.json",
                    json.dumps(payload).encode("utf-8"),
                    content_type="application/json",
                )
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        skill = GlobalAgentSkill.objects.get(name="check-weather")
        self.assertContains(response, "Imported global skill &#x27;check-weather&#x27;.")
        self.assertEqual(skill.description, "Check weather and summarize impact.")
        self.assertTrue(skill.is_active)
        self.assertEqual(skill.tools, ["weather"])
        self.assertEqual(len(skill.secrets), 1)
        tool = skill.bundled_custom_tools.get()
        self.assertEqual(tool.tool_name, "custom_weather_briefing")
        self.assertTrue(tool.source_file.name.endswith("custom_weather_briefing.py"))

    def test_global_agent_skill_import_json_updates_existing_skill_and_replaces_tools(self):
        skill = self._create_global_skill_with_custom_tool(name="check-weather", is_active=False)
        old_tool = skill.bundled_custom_tools.get()
        payload = {
            "name": "check-weather",
            "description": "Updated weather workflow.",
            "tools": ["weather", "read_file"],
            "secrets": [],
            "instructions": "Use the updated workflow.",
            "custom_tools": [
                {
                    "name": "Weather Summary",
                    "tool_name": "weather_summary",
                    "description": "Summarize weather conditions.",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                    "timeout_seconds": 90,
                    "source_code": (
                        "from _gobii_ctx import main\n\n"
                        "def run(params, ctx):\n"
                        "    return {'summary': 'ok'}\n\n"
                        "if __name__ == '__main__':\n"
                        "    main(run)\n"
                    ),
                }
            ],
        }

        response = self.client.post(
            reverse("admin:api_globalagentskill_import_json"),
            data={
                "json_file": SimpleUploadedFile(
                    "skill.json",
                    json.dumps(payload).encode("utf-8"),
                    content_type="application/json",
                )
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        skill.refresh_from_db()
        self.assertContains(response, "Updated global skill &#x27;check-weather&#x27; from JSON.")
        self.assertEqual(skill.description, "Updated weather workflow.")
        self.assertEqual(skill.tools, ["weather", "read_file"])
        self.assertEqual(skill.instructions, "Use the updated workflow.")
        self.assertFalse(skill.is_active)
        self.assertFalse(GlobalAgentSkillCustomTool.objects.filter(pk=old_tool.pk).exists())
        replacement_tool = skill.bundled_custom_tools.get()
        self.assertEqual(replacement_tool.tool_name, "custom_weather_summary")

    def test_global_agent_skill_import_json_rejects_malformed_json(self):
        response = self.client.post(
            reverse("admin:api_globalagentskill_import_json"),
            data={
                "json_file": SimpleUploadedFile(
                    "skill.json",
                    b"{not valid json",
                    content_type="application/json",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid JSON")
        self.assertFalse(GlobalAgentSkill.objects.filter(name="check-weather").exists())

    def test_global_agent_skill_import_json_is_transactional_on_invalid_tool_source(self):
        skill = self._create_global_skill_with_custom_tool(name="check-weather", is_active=False)
        payload = {
            "name": "check-weather",
            "description": "Broken update",
            "tools": ["weather"],
            "secrets": [],
            "instructions": "Broken update instructions.",
            "custom_tools": [
                {
                    "name": "Broken Tool",
                    "tool_name": "broken_tool",
                    "description": "This tool is invalid.",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                    "timeout_seconds": 90,
                    "source_code": "not python at all(",
                }
            ],
        }

        response = self.client.post(
            reverse("admin:api_globalagentskill_import_json"),
            data={
                "json_file": SimpleUploadedFile(
                    "skill.json",
                    json.dumps(payload).encode("utf-8"),
                    content_type="application/json",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "syntax error")
        skill.refresh_from_db()
        self.assertEqual(skill.description, "Check weather")
        self.assertFalse(skill.is_active)
        self.assertEqual(skill.bundled_custom_tools.count(), 1)
        self.assertEqual(skill.bundled_custom_tools.get().tool_name, "custom_weather_tool")

    def test_global_agent_skill_custom_tool_clean_keeps_uploaded_file_readable(self):
        skill = GlobalAgentSkill.objects.create(
            name="weather-check",
            description="Check weather",
            tools=["weather"],
            instructions="Check weather and summarize it.",
        )
        source_bytes = (
            b"from _gobii_ctx import main\n\n"
            b"def run(params, ctx):\n"
            b"    return {'ok': True}\n\n"
            b"if __name__ == '__main__':\n"
            b"    main(run)\n"
        )
        upload = SimpleUploadedFile(
            "weather_tool.py",
            source_bytes,
            content_type="text/x-python",
        )
        tool = GlobalAgentSkillCustomTool(
            global_skill=skill,
            name="Weather Tool",
            tool_name="weather_tool",
            description="Reads weather data.",
            source_file=upload,
            parameters_schema={"type": "object", "properties": {}, "required": []},
            timeout_seconds=120,
        )

        tool.full_clean()

        self.assertFalse(upload.closed)
        self.assertEqual(b"".join(upload.chunks()), source_bytes)

    @patch("util.analytics.Analytics.track_event")
    def test_global_agent_skill_admin_create_emits_analytics(self, mock_track_event):
        admin_view = admin.site._registry[GlobalAgentSkill]
        request = self.request_factory.post("/admin/api/globalagentskill/add/")
        request.user = self.admin_user
        skill = GlobalAgentSkill(
            name="ops-report",
            description="Generate operations reports",
            tools=["sqlite_batch"],
            instructions="Summarize the latest ops metrics.",
        )

        admin_view.save_model(request, skill, form=None, change=False)

        self.assertTrue(GlobalAgentSkill.objects.filter(pk=skill.pk).exists())
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.admin_user.id)
        self.assertEqual(kwargs["event"], AnalyticsEvent.GLOBAL_AGENT_SKILL_CREATED)
        self.assertEqual(kwargs["source"], AnalyticsSource.WEB)
        self.assertEqual(kwargs["properties"]["global_skill_id"], str(skill.id))
        self.assertEqual(kwargs["properties"]["global_skill_name"], "ops-report")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch"])
        self.assertEqual(kwargs["properties"]["tool_count"], 1)
        self.assertTrue(kwargs["properties"]["is_active"])

    @patch("util.analytics.Analytics.track_event")
    def test_global_agent_skill_admin_update_emits_analytics(self, mock_track_event):
        admin_view = admin.site._registry[GlobalAgentSkill]
        request = self.request_factory.post("/admin/api/globalagentskill/change/")
        request.user = self.admin_user
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate operations reports",
            tools=["sqlite_batch"],
            instructions="Summarize the latest ops metrics.",
        )
        skill.instructions = "Summarize the latest ops metrics and blockers."
        skill.tools = ["read_file", "sqlite_batch"]

        admin_view.save_model(request, skill, form=None, change=True)

        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.GLOBAL_AGENT_SKILL_UPDATED)
        self.assertEqual(kwargs["properties"]["tool_ids"], ["read_file", "sqlite_batch"])
        self.assertEqual(kwargs["properties"]["tool_count"], 2)

    @patch("util.analytics.Analytics.track_event")
    def test_global_agent_skill_admin_delete_emits_analytics(self, mock_track_event):
        admin_view = admin.site._registry[GlobalAgentSkill]
        request = self.request_factory.post("/admin/api/globalagentskill/delete/")
        request.user = self.admin_user
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate operations reports",
            tools=["sqlite_batch"],
            instructions="Summarize the latest ops metrics.",
        )

        admin_view.delete_model(request, skill)

        self.assertFalse(GlobalAgentSkill.objects.filter(pk=skill.pk).exists())
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.GLOBAL_AGENT_SKILL_DELETED)
        self.assertEqual(kwargs["properties"]["global_skill_name"], "ops-report")

    @patch("util.analytics.Analytics.track_event")
    def test_global_agent_skill_admin_delete_does_not_emit_analytics_if_delete_fails(self, mock_track_event):
        admin_view = admin.site._registry[GlobalAgentSkill]
        request = self.request_factory.post("/admin/api/globalagentskill/delete/")
        request.user = self.admin_user
        skill = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate operations reports",
            tools=["sqlite_batch"],
            instructions="Summarize the latest ops metrics.",
        )

        with patch.object(admin.ModelAdmin, "delete_model", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                admin_view.delete_model(request, skill)

        mock_track_event.assert_not_called()

    @patch("util.analytics.Analytics.track_event")
    def test_global_agent_skill_admin_bulk_delete_emits_one_event_per_skill(self, mock_track_event):
        admin_view = admin.site._registry[GlobalAgentSkill]
        request = self.request_factory.post("/admin/api/globalagentskill/")
        request.user = self.admin_user
        first = GlobalAgentSkill.objects.create(
            name="ops-report",
            description="Generate operations reports",
            tools=["sqlite_batch"],
            instructions="Summarize the latest ops metrics.",
        )
        second = GlobalAgentSkill.objects.create(
            name="file-review",
            description="Review files consistently",
            tools=["read_file"],
            instructions="Read the file before summarizing it.",
        )

        admin_view.delete_queryset(
            request,
            GlobalAgentSkill.objects.filter(pk__in=[first.pk, second.pk]).order_by("name"),
        )

        self.assertFalse(GlobalAgentSkill.objects.filter(pk__in=[first.pk, second.pk]).exists())
        self.assertEqual(mock_track_event.call_count, 2)
        event_names = [call.kwargs["event"] for call in mock_track_event.call_args_list]
        self.assertEqual(
            event_names,
            [
                AnalyticsEvent.GLOBAL_AGENT_SKILL_DELETED,
                AnalyticsEvent.GLOBAL_AGENT_SKILL_DELETED,
            ],
        )
        deleted_names = [call.kwargs["properties"]["global_skill_name"] for call in mock_track_event.call_args_list]
        self.assertEqual(deleted_names, ["file-review", "ops-report"])

    def test_trigger_processing_skips_expired_agents_when_requested(self):
        expired_agent = self._create_agent(
            life_state=PersistentAgent.LifeState.EXPIRED,
            name="Expired Agent",
        )
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(expired_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                    "skip_expired": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped expired agent ID(s)" in message.message for message in messages))

    def test_trigger_processing_processes_expired_when_skip_unchecked(self):
        expired_agent = self._create_agent(
            life_state=PersistentAgent.LifeState.EXPIRED,
            name="Expired Agent",
        )
        url = reverse("admin:api_persistentagent_trigger_processing")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(expired_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(str(expired_agent.id))

    def test_trigger_processing_skips_missing_user_when_checkbox_on(self):
        url = reverse("admin:api_persistentagent_trigger_processing")

        class _EmptyQuerySet:
            def values_list(self, *args, **kwargs):
                return []

        class _EmptyManager:
            def filter(self, *args, **kwargs):
                return _EmptyQuerySet()

        class _MissingUserModel:
            objects = _EmptyManager()

        with patch("api.admin.get_user_model", return_value=_MissingUserModel), \
                patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={
                    "agent_ids": str(self.persistent_agent.id),
                    "only_active": "on",
                    "only_with_user": "on",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("Skipped agent ID(s) missing a user" in message.message for message in messages))

    def test_force_proactive_get_renders_form(self):
        url = reverse("admin:api_persistentagent_force_proactive", args=[self.persistent_agent.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Force Proactive Outreach")
        self.assertContains(response, str(self.persistent_agent.id))

    def test_force_proactive_post_triggers_outreach(self):
        url = reverse("admin:api_persistentagent_force_proactive", args=[self.persistent_agent.pk])
        reason = " Need immediate outreach "

        with patch("api.admin.ProactiveActivationService.force_trigger") as mock_force, patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"reason": reason}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_force.assert_called_once_with(
            self.persistent_agent,
            initiated_by=self.admin_user.email,
            reason="Need immediate outreach",
        )
        mock_delay.assert_called_once_with(str(self.persistent_agent.pk))
        messages = list(response.context["messages"])
        self.assertTrue(any("Forced proactive outreach queued" in message.message for message in messages))

    def test_force_proactive_post_handles_value_error(self):
        url = reverse("admin:api_persistentagent_force_proactive", args=[self.persistent_agent.pk])
        reason = "Inactive owner"

        with patch("api.admin.ProactiveActivationService.force_trigger", side_effect=ValueError("owner inactive")) as mock_force, patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"reason": reason}, follow=True)

        self.assertEqual(response.status_code, 200)
        mock_force.assert_called_once()
        mock_delay.assert_not_called()
        messages = list(response.context["messages"])
        self.assertTrue(any("owner inactive" in message.message for message in messages))

    def test_change_view_renders_with_message_inline(self):
        agent_ep = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.persistent_agent,
            channel="email",
            address="agent@example.com",
            is_primary=True,
        )
        external_ep = PersistentAgentCommsEndpoint.objects.create(
            channel="email",
            address="external@example.com",
            is_primary=True,
        )
        PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=agent_ep,
            to_endpoint=external_ep,
            body="Hi there",
        )

        url = reverse("admin:api_persistentagent_change", args=[self.persistent_agent.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_save_model_soft_delete_removes_peer_links_and_preserves_history(self):
        peer_agent = self._create_agent(name="Admin Deleted Peer")
        peer_link = AgentPeerLink.objects.create(
            agent_a=self.persistent_agent,
            agent_b=peer_agent,
            created_by=self.admin_user,
        )
        peer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=peer_agent,
            channel="other",
            address=f"peer-{peer_agent.id}",
            is_primary=True,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.persistent_agent,
            channel="other",
            address=f"peer-{peer_agent.id}",
            is_peer_dm=True,
            peer_link=peer_link,
        )
        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=peer_endpoint,
            conversation=conversation,
            body="Peer history survives admin delete",
            owner_agent=self.persistent_agent,
            peer_agent=peer_agent,
        )
        model_admin = PersistentAgentAdmin(PersistentAgent, admin.site)
        request = self.request_factory.post("/")
        request.user = self.admin_user
        form = type("FormStub", (), {"changed_data": ["is_deleted"]})()

        peer_agent.is_deleted = True
        model_admin.save_model(request, peer_agent, form, change=True)

        self.assertFalse(AgentPeerLink.objects.filter(id=peer_link.id).exists())
        conversation.refresh_from_db()
        self.assertIsNone(conversation.peer_link_id)
        self.assertFalse(conversation.is_peer_dm)
        self.assertTrue(PersistentAgentMessage.objects.filter(id=message.id).exists())

    def test_system_message_get_renders_form(self):
        url = reverse("admin:api_persistentagent_system_message", args=[self.persistent_agent.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Issue System Message")
        self.assertContains(response, str(self.persistent_agent.id))

    def test_system_message_post_creates_record_and_triggers_processing(self):
        url = reverse("admin:api_persistentagent_system_message", args=[self.persistent_agent.pk])

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(url, data={"message": "Focus on the quarterly report"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PersistentAgentSystemMessage.objects.filter(
                agent=self.persistent_agent,
                body="Focus on the quarterly report",
            ).exists()
        )
        mock_delay.assert_called_once_with(str(self.persistent_agent.pk))
        messages = list(response.context["messages"])
        self.assertTrue(any("System message saved" in message.message for message in messages))

    def test_system_message_broadcast_get_renders_form(self):
        url = reverse("admin:api_persistentagentsystemmessagebroadcast_add")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Broadcast System Message")
        self.assertContains(response, "Saving will")

    def test_system_message_broadcast_creates_records_without_processing(self):
        extra_agent = self._create_agent(name="Second Agent")
        url = reverse("admin:api_persistentagentsystemmessagebroadcast_add")

        with patch("api.admin.process_agent_events_task.delay") as mock_delay:
            response = self.client.post(
                url,
                data={"body": "Global directive", "_save": "Save"},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_delay.assert_not_called()
        broadcast = PersistentAgentSystemMessageBroadcast.objects.get()
        self.assertEqual(broadcast.body, "Global directive")
        for agent in (self.persistent_agent, extra_agent):
            self.assertTrue(
                PersistentAgentSystemMessage.objects.filter(
                    agent=agent, body="Global directive", broadcast=broadcast
                ).exists()
            )

        messages = list(response.context["messages"])
        self.assertTrue(any("Broadcast saved for 2 persistent agents" in message.message for message in messages))

    def test_broadcast_changelist_lists_entries(self):
        broadcast = PersistentAgentSystemMessageBroadcast.objects.create(
            body="hello",
            created_by=self.admin_user,
        )
        PersistentAgentSystemMessage.objects.create(
            agent=self.persistent_agent,
            body="hello",
            created_by=self.admin_user,
            broadcast=broadcast,
        )

        url = reverse("admin:api_persistentagentsystemmessagebroadcast_changelist")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hello")

    def test_broadcast_edit_updates_system_messages(self):
        extra_agent = self._create_agent(name="Another Agent")
        broadcast = PersistentAgentSystemMessageBroadcast.objects.create(
            body="original",
            created_by=self.admin_user,
        )
        for agent in (self.persistent_agent, extra_agent):
            PersistentAgentSystemMessage.objects.create(
                agent=agent,
                body="original",
                created_by=self.admin_user,
                broadcast=broadcast,
            )

        url = reverse("admin:api_persistentagentsystemmessagebroadcast_change", args=[broadcast.pk])
        response = self.client.post(
            url,
            data={"body": "updated broadcast", "_save": "Save"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        for agent in (self.persistent_agent, extra_agent):
            self.assertTrue(
                PersistentAgentSystemMessage.objects.filter(
                    agent=agent,
                    broadcast=broadcast,
                    body="updated broadcast",
                ).exists()
            )

        messages = list(response.context["messages"])
        self.assertTrue(any("Broadcast updated" in message.message for message in messages))

    def test_broadcast_edit_skips_delivered_messages(self):
        broadcast = PersistentAgentSystemMessageBroadcast.objects.create(
            body="initial",
            created_by=self.admin_user,
        )
        delivered_message = PersistentAgentSystemMessage.objects.create(
            agent=self.persistent_agent,
            body="initial",
            created_by=self.admin_user,
            broadcast=broadcast,
            delivered_at=timezone.now(),
        )
        pending_message = PersistentAgentSystemMessage.objects.create(
            agent=self._create_agent(name="Pending Agent"),
            body="initial",
            created_by=self.admin_user,
            broadcast=broadcast,
        )

        url = reverse("admin:api_persistentagentsystemmessagebroadcast_change", args=[broadcast.pk])
        response = self.client.post(
            url,
            data={"body": "new text", "_save": "Save"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        delivered_message.refresh_from_db()
        pending_message.refresh_from_db()
        self.assertEqual(delivered_message.body, "initial")
        self.assertEqual(pending_message.body, "new text")
