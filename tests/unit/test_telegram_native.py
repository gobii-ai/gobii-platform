import json
from io import BytesIO
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from api.agent.system_skills.defaults import TELEGRAM_NATIVE_SYSTEM_SKILL_KEY
from api.agent.tools.send_telegram_message import execute_send_telegram_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSystemStep,
    PersistentAgentSystemSkillState,
    PersistentAgentTelegramBotIdentity,
    PersistentAgentTelegramChatBinding,
    PersistentAgentTelegramProvisioningSession,
    PersistentAgentTelegramUpdateReceipt,
    PersistentAgentTelegramUserLink,
    PersistentAgentTelegramUserLinkRequest,
)
from api.services.telegram_bot import (
    build_telegram_manager_link_url,
    complete_managed_bot_provisioning,
    disconnect_telegram_native_integration,
    start_telegram_connect,
    sync_telegram_bot_profile,
)


def _response(payload=None, status_code=200, content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    response.content = content
    response.headers = {"Content-Type": "application/json", "Content-Length": str(len(content))}
    response.raise_for_status.return_value = None
    response.ok = status_code < 400
    response.text = json.dumps(payload or {})
    return response


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (8, 8), (30, 120, 220, 180)).save(output, format="PNG")
    return output.getvalue()


@override_settings(
    PUBLIC_SITE_URL="https://app.example.test",
    TELEGRAM_MANAGER_BOT_TOKEN="manager-token",
    TELEGRAM_MANAGER_BOT_USERNAME="GobiiManagerBot",
    TELEGRAM_MANAGER_WEBHOOK_SECRET="manager-secret",
    TELEGRAM_INBOUND_DEBOUNCE_SECONDS=0,
    CELERY_TASK_ALWAYS_EAGER=True,
)
class NativeTelegramTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="telegram-owner",
            email="telegram-owner@example.test",
            password="pw",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Telegram Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Telegram Agent",
            charter="Handle Telegram messages.",
            browser_use_agent=cls.browser_agent,
        )

    def _force_login_console_manager(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

    def _user_link(self, telegram_user_id="500"):
        return PersistentAgentTelegramUserLink.objects.create(
            owner_user=self.user,
            telegram_user_id=telegram_user_id,
            username="tg_owner",
            linked_by=self.user,
            last_seen_at=timezone.now(),
        )

    def _identity(self):
        identity = PersistentAgentTelegramBotIdentity.objects.create(
            agent=self.agent,
            telegram_bot_id="900",
            username="telegram_agent_bot",
            display_name="Telegram Agent",
            status=PersistentAgentTelegramBotIdentity.Status.ACTIVE,
            connected_at=timezone.now(),
        )
        identity.token = "agent-token"
        identity.webhook_secret = "agent-secret"
        identity.save(update_fields=["token_encrypted", "webhook_secret_encrypted", "updated_at"])
        return identity

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot._telegram_request", return_value={"id": 1, "can_manage_bots": True})
    def test_manager_start_links_telegram_user_to_owner(self, request_mock):
        token_url = build_telegram_manager_link_url(self.agent, self.user)
        token = parse_qs(urlsplit(token_url).query)["start"][0]
        self.assertLessEqual(len(token), 64)

        response = self.client.post(
            reverse("telegram_manager_webhook"),
            data=json.dumps({
                "update_id": 1,
                "message": {
                    "text": f"/start {token}",
                    "from": {"id": 500, "username": "tg_owner", "first_name": "Tara"},
                    "chat": {"id": 500, "type": "private"},
                },
            }),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="manager-secret",
        )

        self.assertEqual(response.status_code, 200, response.content.decode("utf-8"))
        link = PersistentAgentTelegramUserLink.objects.get(owner_user=self.user)
        self.assertEqual(link.telegram_user_id, "500")
        self.assertEqual(link.username, "tg_owner")
        link_request = PersistentAgentTelegramUserLinkRequest.objects.get(token=token)
        self.assertIsNotNone(link_request.used_at)
        self.assertEqual(request_mock.call_args.args[1], "sendMessage")
        self.assertEqual(request_mock.call_args.kwargs["payload"]["chat_id"], "500")
        self.assertIn("Telegram is linked", request_mock.call_args.kwargs["payload"]["text"])

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot._telegram_request", return_value={"message_id": 1})
    def test_manager_start_without_token_replies_with_instructions(self, request_mock):
        response = self.client.post(
            reverse("telegram_manager_webhook"),
            data=json.dumps({
                "update_id": 1,
                "message": {
                    "text": "/start",
                    "from": {"id": 500, "username": "tg_owner", "first_name": "Tara"},
                    "chat": {"id": 500, "type": "private"},
                },
            }),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="manager-secret",
        )

        self.assertEqual(response.status_code, 200, response.content.decode("utf-8"))
        self.assertTrue(response.json()["ignored"])
        self.assertEqual(response.json()["reason"], "missing_start_token")
        self.assertEqual(request_mock.call_args.args[1], "sendMessage")
        self.assertIn("full /start command", request_mock.call_args.kwargs["payload"]["text"])

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot._telegram_request", return_value={"id": 1, "can_manage_bots": True})
    def test_connect_requires_link_then_creates_pending_managed_bot_session(self, request_mock):
        self._force_login_console_manager()
        response = self.client.post(reverse("console-agent-telegram-connect", args=[self.agent.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "link_required")

        self._user_link()
        response = self.client.post(reverse("console-agent-telegram-connect", args=[self.agent.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "create_required")
        self.assertIn("https://t.me/newbot/GobiiManagerBot/", payload["create_bot_url"])
        self.assertTrue(PersistentAgentTelegramProvisioningSession.objects.filter(agent=self.agent).exists())

    @tag("telegram_native_batch")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    @patch("api.services.telegram_bot.sync_telegram_bot_profile", return_value={"status": "success"})
    @patch("api.services.telegram_bot._telegram_request")
    @patch("api.services.telegram_bot._fetch_managed_bot_token", return_value="managed-agent-token")
    def test_managed_bot_update_completes_provisioning(self, token_mock, request_mock, sync_mock, delay_mock):
        user_link = self._user_link("500")
        PersistentAgentTelegramProvisioningSession.objects.create(
            agent=self.agent,
            user_link=user_link,
            owner_user=self.user,
            initiated_by=self.user,
            suggested_username="telegram_agent_bot",
            suggested_name="Telegram Agent",
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        request_mock.return_value = {"id": 900, "username": "telegram_agent_bot"}

        with self.captureOnCommitCallbacks(execute=True):
            result = complete_managed_bot_provisioning({
                "update_id": 2,
                "managed_bot": {
                    "user": {"id": 500},
                    "bot": {"id": 900, "username": "telegram_agent_bot", "first_name": "Telegram Agent"},
                },
            })

        self.assertFalse(result["ignored"])
        identity = PersistentAgentTelegramBotIdentity.objects.get(agent=self.agent)
        self.assertEqual(identity.telegram_bot_id, "900")
        self.assertEqual(identity.username, "telegram_agent_bot")
        self.assertEqual(identity.token, "managed-agent-token")
        self.assertEqual(identity.status, PersistentAgentTelegramBotIdentity.Status.ACTIVE)
        self.assertTrue(PersistentAgentSystemSkillState.objects.filter(
            agent=self.agent,
            skill_key=TELEGRAM_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=True,
        ).exists())
        system_step = PersistentAgentSystemStep.objects.get(
            step__agent=self.agent,
            code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
        )
        self.assertIn("Telegram connection completed", system_step.step.description)
        self.assertIn("@telegram_agent_bot", system_step.step.description)
        self.assertIn("telegram_chats", system_step.step.description)
        self.assertIn("send_telegram_message", system_step.step.description)
        self.assertIn('"bot_id":"900"', system_step.notes)
        self.assertIn('"bot_username":"telegram_agent_bot"', system_step.notes)
        delay_mock.assert_called_once_with(str(self.agent.id))
        self.assertTrue(sync_mock.called)

    @tag("telegram_native_batch")
    def test_managed_bot_update_without_session_is_ignored(self):
        result = complete_managed_bot_provisioning({
            "update_id": 2,
            "managed_bot": {
                "user": {"id": 500},
                "bot": {"id": 900, "username": "telegram_agent_bot"},
            },
        })
        self.assertTrue(result["ignored"])
        self.assertEqual(result["reason"], "ambiguous_session")

    @tag("telegram_native_batch")
    @patch("api.agent.tasks.process_events.process_agent_events_task.delay")
    def test_agent_webhook_validates_secret_dedupes_and_ingests_private_dm(self, delay_mock):
        identity = self._identity()
        url = reverse("telegram_agent_bot_webhook", args=[identity.id])
        payload = {
            "update_id": 10,
            "message": {
                "message_id": 77,
                "text": "hello agent",
                "from": {"id": 500, "username": "tg_owner", "is_bot": False},
                "chat": {"id": 500, "type": "private", "username": "tg_owner", "first_name": "Tara"},
            },
        }

        bad_response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
        self.assertEqual(bad_response.status_code, 403)

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="agent-secret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ignored"])
        message = PersistentAgentMessage.objects.get(owner_agent=self.agent, body="hello agent")
        self.assertEqual(message.conversation.channel, CommsChannel.TELEGRAM)
        self.assertEqual(message.raw_payload["telegram_chat_id"], "500")
        self.assertTrue(PersistentAgentTelegramUpdateReceipt.objects.filter(bot_identity=identity, update_id=10).exists())
        binding = PersistentAgentTelegramChatBinding.objects.get(agent=self.agent, chat_id="500")
        self.assertEqual(binding.chat_type, "private")

        duplicate = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="agent-secret",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["ignored"])
        self.assertEqual(duplicate.json()["reason"], "duplicate_update")

    @tag("telegram_native_batch")
    def test_agent_webhook_ignores_bot_sender(self):
        identity = self._identity()
        response = self.client.post(
            reverse("telegram_agent_bot_webhook", args=[identity.id]),
            data=json.dumps({
                "update_id": 11,
                "message": {
                    "message_id": 78,
                    "text": "loop",
                    "from": {"id": 901, "is_bot": True},
                    "chat": {"id": -100, "type": "group", "title": "Ops"},
                },
            }),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="agent-secret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ignored"])
        self.assertEqual(response.json()["reason"], "bot_sender")

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot._telegram_request", return_value={"message_id": 88})
    def test_send_telegram_message_uses_agent_bot_token_and_records_outbound(self, request_mock):
        identity = self._identity()
        binding = PersistentAgentTelegramChatBinding.objects.create(
            agent=self.agent,
            bot_identity=identity,
            chat_id="500",
            chat_type="private",
            title="Tara",
        )

        result = execute_send_telegram_message(
            self.agent,
            {
                "chat_binding_id": str(binding.id),
                "message": "Reply from Gobii",
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "success")
        request_mock.assert_called_once()
        self.assertEqual(request_mock.call_args.args[0], "agent-token")
        self.assertEqual(request_mock.call_args.args[1], "sendMessage")
        stored = PersistentAgentMessage.objects.get(id=result["message_id"])
        self.assertTrue(stored.is_outbound)
        self.assertEqual(stored.raw_payload["telegram_message_id"], "88")

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot._delete_agent_bot_webhook")
    def test_disconnect_marks_identity_bindings_and_skill_inactive(self, delete_mock):
        identity = self._identity()
        PersistentAgentTelegramChatBinding.objects.create(
            agent=self.agent,
            bot_identity=identity,
            chat_id="500",
            chat_type="private",
            title="Tara",
        )
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=TELEGRAM_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )

        result = disconnect_telegram_native_integration(self.agent)

        self.assertEqual(result["bot_disconnected"], 1)
        identity.refresh_from_db()
        self.assertEqual(identity.status, PersistentAgentTelegramBotIdentity.Status.DISCONNECTED)
        self.assertFalse(PersistentAgentTelegramChatBinding.objects.filter(
            agent=self.agent,
            status=PersistentAgentTelegramChatBinding.Status.ACTIVE,
        ).exists())
        self.assertFalse(PersistentAgentSystemSkillState.objects.get(
            agent=self.agent,
            skill_key=TELEGRAM_NATIVE_SYSTEM_SKILL_KEY,
        ).is_enabled)

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot.build_public_agent_avatar_thumbnail_url", return_value="https://app.example.test/avatar.png")
    @patch("api.services.telegram_bot.requests.get")
    @patch("api.services.telegram_bot._telegram_request")
    def test_profile_sync_uploads_avatar_as_telegram_static_profile_photo(self, request_mock, get_mock, avatar_url_mock):
        identity = self._identity()
        get_mock.return_value = _response(status_code=200, content=_png_bytes())
        request_mock.return_value = {"result": True}

        result = sync_telegram_bot_profile(identity)

        self.assertEqual(result["status"], "success")
        profile_photo_call = next(call for call in request_mock.call_args_list if call.args[1] == "setMyProfilePhoto")
        self.assertEqual(
            profile_photo_call.kwargs["payload"]["photo"],
            {"type": "static", "photo": "attach://photo"},
        )
        uploaded = profile_photo_call.kwargs["files"]["photo"]
        self.assertEqual(uploaded[0], "avatar.jpg")
        self.assertEqual(uploaded[2], "image/jpeg")
        self.assertTrue(uploaded[1].startswith(b"\xff\xd8"))

    @tag("telegram_native_batch")
    @patch("api.services.telegram_bot.sync_telegram_bot_profile", return_value={"status": "success"})
    def test_agent_profile_update_auto_syncs_active_telegram_bot(self, sync_mock):
        identity = self._identity()

        with self.captureOnCommitCallbacks(execute=True):
            self.agent.name = "Renamed Telegram Agent"
            self.agent.save(update_fields=["name"])

        sync_mock.assert_called_once()
        self.assertEqual(sync_mock.call_args.args[0].id, identity.id)
