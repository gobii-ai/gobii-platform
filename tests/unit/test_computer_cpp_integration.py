import asyncio
import json
import tempfile
import uuid
from contextlib import ExitStack
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings, tag
from django.urls import reverse
from waffle import get_waffle_flag_model

from api.agent.system_skills.registry import shortlist_system_skills
from api.computer_consumers import ComputerRelayConsumer
from api.models import (
    BrowserUseAgent,
    ComputerDevice,
    ComputerDeviceApp,
    ComputerDeviceAssignment,
    ComputerDeviceCredential,
    ComputerPairingSession,
    MCPServerConfig,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentMCPServer,
    PersistentAgentSystemSkillState,
)
from api.services.computer_relay import (
    COMPUTER_CPP_WAFFLE_FLAG,
    ComputerRelayError,
    authenticate_relay_access_token,
    consume_artifact,
    create_pairing_session,
    get_device_presence,
    store_artifact,
    sync_device_manifest,
    relay_mcp_request,
    serialize_device,
)


def _create_agent(user, name="Desktop agent", organization=None):
    with ExitStack() as stack:
        stack.enter_context(patch.object(BrowserUseAgent, "select_random_proxy", return_value=None))
        if organization is not None:
            stack.enter_context(patch.object(PersistentAgent, "_validate_org_seats", return_value=None))
        browser = BrowserUseAgent.objects.create(user=user, name=f"{name} browser")
        return PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser,
        )


def _enable_computer_flag(user):
    flag, _ = get_waffle_flag_model().objects.get_or_create(
        name=COMPUTER_CPP_WAFFLE_FLAG,
        defaults={"everyone": False},
    )
    flag.users.add(user)
    return flag


def _pairing_payload(machine_id=None):
    return {
        "machine_id": machine_id or f"machine-{uuid.uuid4()}",
        "display_name": "Matt's Mac",
        "platform": "macos",
        "architecture": "arm64",
        "client_version": "0.21.0",
        "protocol_version": 1,
        "apps": [
            {
                "key": "gobii-desktop",
                "display_name": "Gobii Desktop",
                "type": "bundled",
                "schema_sha256": "a" * 64,
            },
            {
                "key": "custom-tools",
                "display_name": "Custom Tools",
                "type": "custom",
                "schema_sha256": "b" * 64,
            },
        ],
    }


@tag("computer_cpp_batch")
class ComputerPairingAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="computer-owner",
            email="computer-owner@example.com",
            password="test-password",
        )
        self.agent = _create_agent(self.user)

    def test_console_api_is_hidden_when_user_flag_is_off(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("console-computer-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"enabled": False})

    def test_pairing_start_is_available_without_flag_but_approval_is_not(self):
        response = self.client.post(
            reverse("computer-pairing-start"),
            data=json.dumps(_pairing_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("computer_pairing=", response.json()["verification_uri_complete"])

        self.client.force_login(self.user)
        approval = self.client.post(
            reverse(
                "console-computer-pairing",
                kwargs={"pairing_id": response.json()["pairing_id"]},
            ),
            data=json.dumps(
                {
                    "user_code": response.json()["user_code"],
                    "agent_id": str(self.agent.id),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(approval.status_code, 403)

    def test_pairing_creates_owned_device_assignment_and_managed_mcp(self):
        _enable_computer_flag(self.user)
        start = self.client.post(
            reverse("computer-pairing-start"),
            data=json.dumps(_pairing_payload()),
            content_type="application/json",
        )
        self.assertEqual(start.status_code, 201)
        pairing_data = start.json()

        self.client.force_login(self.user)
        approval = self.client.post(
            reverse(
                "console-computer-pairing",
                kwargs={"pairing_id": pairing_data["pairing_id"]},
            ),
            data=json.dumps(
                {
                    "user_code": pairing_data["user_code"],
                    "agent_id": str(self.agent.id),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(approval.status_code, 200)

        self.client.logout()
        exchange = self.client.post(
            reverse(
                "computer-pairing-exchange",
                kwargs={"pairing_id": pairing_data["pairing_id"]},
            ),
            data=json.dumps({"device_code": pairing_data["device_code"]}),
            content_type="application/json",
        )
        self.assertEqual(exchange.status_code, 200)
        self.assertNotIn(pairing_data["device_code"], str(ComputerPairingSession.objects.values()))

        device = ComputerDevice.objects.get(id=exchange.json()["device_id"])
        self.assertEqual(device.owner, self.user)
        assignment = ComputerDeviceAssignment.objects.get(device=device)
        self.assertEqual(assignment.agent, self.agent)
        self.assertEqual(assignment.status, ComputerDeviceAssignment.Status.ACTIVE)

        bundled = device.apps.get(app_key="gobii-desktop")
        custom = device.apps.get(app_key="custom-tools")
        self.assertEqual(bundled.approval_state, ComputerDeviceApp.ApprovalState.APPROVED)
        self.assertEqual(custom.approval_state, ComputerDeviceApp.ApprovalState.PENDING)
        self.assertEqual(bundled.mcp_server_config.transport, MCPServerConfig.Transport.COMPUTER_RELAY)
        self.assertTrue(bundled.mcp_server_config.name.startswith(f"computer_{device.id.hex[:8]}_"))
        self.assertEqual(bundled.mcp_server_config.user, self.user)
        self.assertTrue(
            PersistentAgentMCPServer.objects.filter(
                agent=self.agent,
                server_config=bundled.mcp_server_config,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="computer",
                is_enabled=True,
            ).exists()
        )

        ComputerPairingSession.objects.filter(id=pairing_data["pairing_id"]).update(
            last_polled_at=None
        )
        replay = self.client.post(
            reverse(
                "computer-pairing-exchange",
                kwargs={"pairing_id": pairing_data["pairing_id"]},
            ),
            data=json.dumps({"device_code": pairing_data["device_code"]}),
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["error"], "already_redeemed")

    def test_refresh_rotates_and_replay_revokes_the_credential_family(self):
        _enable_computer_flag(self.user)
        pairing, device_code, user_code = create_pairing_session(_pairing_payload())
        from api.services.computer_relay import approve_pairing, redeem_pairing

        approve_pairing(
            pairing,
            user=self.user,
            user_code=user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        device, refresh_token, _ = redeem_pairing(pairing, device_code=device_code)
        original_generation = device.credential_generation

        refresh = self.client.post(
            reverse("computer-token-refresh"),
            data=json.dumps({"refresh_token": refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(refresh.status_code, 200)
        self.assertNotEqual(refresh.json()["refresh_token"], refresh_token)

        replay = self.client.post(
            reverse("computer-token-refresh"),
            data=json.dumps({"refresh_token": refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 401)
        device.refresh_from_db()
        self.assertEqual(device.credential_generation, original_generation + 1)
        self.assertFalse(
            ComputerDeviceCredential.objects.filter(
                device=device,
                revoked_at__isnull=True,
            ).exists()
        )
        with self.assertRaises(PermissionError):
            authenticate_relay_access_token(refresh.json()["access_token"])

    @override_settings(COMPUTER_CPP_REFRESHES_PER_DEVICE_HOUR=1)
    def test_refresh_rate_limit_follows_device_across_token_rotation(self):
        _enable_computer_flag(self.user)
        pairing, device_code, user_code = create_pairing_session(_pairing_payload())
        from api.services.computer_relay import approve_pairing, redeem_pairing

        approve_pairing(
            pairing,
            user=self.user,
            user_code=user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        _, refresh_token, _ = redeem_pairing(pairing, device_code=device_code)

        first = self.client.post(
            reverse("computer-token-refresh"),
            data=json.dumps({"refresh_token": refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            reverse("computer-token-refresh"),
            data=json.dumps({"refresh_token": first.json()["refresh_token"]}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"], "rate_limited")

    @patch("api.services.computer_relay.get_device_presence", return_value=None)
    def test_managed_configs_are_hidden_from_generic_mcp_api(self, _presence):
        _enable_computer_flag(self.user)
        pairing, device_code, user_code = create_pairing_session(_pairing_payload())
        from api.services.computer_relay import approve_pairing, redeem_pairing

        approve_pairing(
            pairing,
            user=self.user,
            user_code=user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        device, _, _ = redeem_pairing(pairing, device_code=device_code)
        managed_config = device.apps.get(app_key="gobii-desktop").mcp_server_config

        self.client.force_login(self.user)
        listing = self.client.get(reverse("console-mcp-server-list"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn(str(managed_config.id), {row["id"] for row in listing.json()["servers"]})

        detail = self.client.get(
            reverse("console-mcp-server-detail", kwargs={"server_id": managed_config.id})
        )
        self.assertEqual(detail.status_code, 403)

        from api.services.mcp_servers import set_server_assignments, update_agent_personal_servers

        regular_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.USER,
            user=self.user,
            name="editable-server",
            display_name="Editable server",
            url="https://example.com/mcp",
        )
        update_agent_personal_servers(self.agent, [str(regular_config.id)])
        self.assertTrue(
            PersistentAgentMCPServer.objects.filter(
                agent=self.agent,
                server_config=managed_config,
            ).exists()
        )
        with self.assertRaisesRegex(ValueError, "cannot be assigned manually"):
            set_server_assignments(managed_config, [])

    def test_repairing_reapplies_selected_apps_to_existing_device(self):
        _enable_computer_flag(self.user)
        machine_id = f"repair-{uuid.uuid4()}"
        from api.services.computer_relay import approve_pairing, redeem_pairing

        first_pairing, first_device_code, first_user_code = create_pairing_session(
            _pairing_payload(machine_id)
        )
        approve_pairing(
            first_pairing,
            user=self.user,
            user_code=first_user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        device, _, _ = redeem_pairing(first_pairing, device_code=first_device_code)

        second_pairing, second_device_code, second_user_code = create_pairing_session(
            _pairing_payload(machine_id)
        )
        approve_pairing(
            second_pairing,
            user=self.user,
            user_code=second_user_code,
            agent=self.agent,
            selected_app_keys=["custom-tools"],
        )
        repaired, _, _ = redeem_pairing(second_pairing, device_code=second_device_code)

        self.assertEqual(repaired.id, device.id)
        self.assertEqual(
            repaired.apps.get(app_key="gobii-desktop").approval_state,
            ComputerDeviceApp.ApprovalState.PENDING,
        )
        self.assertEqual(
            repaired.apps.get(app_key="custom-tools").approval_state,
            ComputerDeviceApp.ApprovalState.APPROVED,
        )


@tag("computer_cpp_batch")
class ComputerRelayLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="relay-owner",
            email="relay-owner@example.com",
        )
        self.agent = _create_agent(self.user, "Relay agent")
        _enable_computer_flag(self.user)
        self.pairing, self.device_code, self.user_code = create_pairing_session(_pairing_payload())
        from api.services.computer_relay import approve_pairing, redeem_pairing

        approve_pairing(
            self.pairing,
            user=self.user,
            user_code=self.user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        self.device, _, self.access_token = redeem_pairing(
            self.pairing,
            device_code=self.device_code,
        )

    def test_schema_change_suspends_app_and_invalidates_assignment(self):
        app = self.device.apps.get(app_key="gobii-desktop")
        config = app.mcp_server_config

        changed_manifest = _pairing_payload()["apps"]
        changed_manifest[0]["schema_sha256"] = "c" * 64
        sync_device_manifest(self.device, changed_manifest)

        app.refresh_from_db()
        config.refresh_from_db()
        self.assertEqual(app.approval_state, ComputerDeviceApp.ApprovalState.PENDING)
        self.assertFalse(config.is_active)
        self.assertFalse(
            PersistentAgentMCPServer.objects.filter(server_config=config).exists()
        )

    def test_flag_removal_rejects_existing_relay_access_token(self):
        flag = get_waffle_flag_model().objects.get(name=COMPUTER_CPP_WAFFLE_FLAG)
        flag.users.remove(self.user)

        with self.assertRaises(PermissionError):
            authenticate_relay_access_token(self.access_token)

    @override_settings(MEDIA_ROOT=tempfile.gettempdir())
    def test_artifacts_are_type_checked_device_scoped_and_single_use(self):
        upload = SimpleUploadedFile(
            "screen.png",
            b"\x89PNG\r\n\x1a\nfake-image",
            content_type="image/png",
        )
        artifact = store_artifact(self.device, upload)
        other_device = ComputerDevice.objects.create(
            owner=self.user,
            machine_identifier_digest="d" * 64,
            display_name="Other Mac",
            platform=ComputerDevice.Platform.MACOS,
            architecture="arm64",
            client_version="0.21.0",
            protocol_version=1,
        )

        with self.assertRaises(ComputerRelayError):
            consume_artifact(other_device.id, artifact.id)
        image = consume_artifact(self.device.id, artifact.id)
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        with self.assertRaises(ComputerRelayError):
            consume_artifact(self.device.id, artifact.id)

        invalid = SimpleUploadedFile(
            "not-really.png",
            b"not an image",
            content_type="image/png",
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            store_artifact(self.device, invalid)

    def test_agent_deactivation_revokes_assignment_but_retains_device(self):
        self.agent.is_active = False
        self.agent.save(update_fields=["is_active"])

        assignment = ComputerDeviceAssignment.objects.get(device=self.device)
        self.assertEqual(assignment.status, ComputerDeviceAssignment.Status.REVOKED)
        self.assertTrue(ComputerDevice.objects.filter(id=self.device.id).exists())
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="computer",
                is_enabled=True,
            ).exists()
        )

    def test_team_manager_can_revoke_grant_but_cannot_manage_personal_device(self):
        organization = Organization.objects.create(
            name="Computer Team",
            slug=f"computer-team-{uuid.uuid4().hex[:8]}",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        team_agent = _create_agent(self.user, "Team desktop agent", organization)
        from api.services.computer_relay import assign_device

        assign_device(self.device, team_agent, granted_by=self.user)
        manager = get_user_model().objects.create_user(
            username="computer-manager",
            email="computer-manager@example.com",
        )
        OrganizationMembership.objects.create(
            org=organization,
            user=manager,
            role=OrganizationMembership.OrgRole.ADMIN,
        )
        _enable_computer_flag(manager)
        self.client.force_login(manager)

        manage = self.client.patch(
            reverse("console-computer-detail", kwargs={"device_id": self.device.id}),
            data=json.dumps({"display_name": "Renamed by manager"}),
            content_type="application/json",
        )
        self.assertEqual(manage.status_code, 404)

        revoke = self.client.delete(
            reverse("console-computer-assignment", kwargs={"device_id": self.device.id})
        )
        self.assertEqual(revoke.status_code, 200)
        assignment = ComputerDeviceAssignment.objects.get(device=self.device)
        self.assertEqual(assignment.status, ComputerDeviceAssignment.Status.REVOKED)
        app = self.device.apps.get(app_key="gobii-desktop")
        app.mcp_server_config.refresh_from_db()
        self.assertFalse(app.mcp_server_config.is_active)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)

    def test_owner_role_downgrade_revokes_team_assignment(self):
        organization = Organization.objects.create(
            name="Role downgrade team",
            slug=f"role-downgrade-{uuid.uuid4().hex[:8]}",
            created_by=self.user,
        )
        membership = OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        team_agent = _create_agent(self.user, "Role downgrade agent", organization)
        from api.services.computer_relay import assign_device

        assign_device(self.device, team_agent, granted_by=self.user)
        with (
            patch("api.services.computer_relay._queue_agent_resume"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            membership.role = OrganizationMembership.OrgRole.MEMBER
            membership.save(update_fields=["role"])

        self.assertEqual(
            ComputerDeviceAssignment.objects.get(device=self.device).status,
            ComputerDeviceAssignment.Status.REVOKED,
        )

    @patch("api.services.computer_relay.get_device_presence", return_value=None)
    def test_prefetched_apps_serialize_without_additional_queries(self, _presence):
        device = (
            ComputerDevice.objects.select_related(
                "owner",
                "assignment__agent",
                "assignment__organization",
            )
            .prefetch_related("apps")
            .get(id=self.device.id)
        )

        with self.assertNumQueries(0):
            payload = serialize_device(device, owner_actions=True)

        self.assertEqual(len(payload["apps"]), 2)

    def test_computer_skill_is_searchable_without_enabling_all_mcp_tools(self):
        results = shortlist_system_skills(
            "take a screenshot of my desktop computer",
            available_tool_names=set(),
        )

        self.assertIn("computer", [definition.skill_key for definition in results])


@tag("computer_cpp_batch")
class MCPTransportModelTests(TestCase):
    def test_legacy_command_and_url_writes_get_explicit_transport(self):
        command = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="legacy-command",
            display_name="Legacy command",
            command="npx",
        )
        http = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="legacy-http",
            display_name="Legacy HTTP",
            url="https://example.com/mcp",
        )

        self.assertEqual(command.transport, MCPServerConfig.Transport.STDIO)
        self.assertEqual(http.transport, MCPServerConfig.Transport.STREAMABLE_HTTP)


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
@tag("computer_cpp_batch")
class ComputerRelayWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="socket-owner",
            email="socket-owner@example.com",
        )
        self.agent = _create_agent(self.user, "Socket agent")
        _enable_computer_flag(self.user)
        pairing, device_code, user_code = create_pairing_session(_pairing_payload())
        from api.services.computer_relay import approve_pairing, redeem_pairing

        approve_pairing(
            pairing,
            user=self.user,
            user_code=user_code,
            agent=self.agent,
            selected_app_keys=["gobii-desktop"],
        )
        with (
            patch("api.services.computer_relay._queue_agent_resume"),
            patch("api.agent.tools.mcp_manager.get_mcp_manager"),
        ):
            self.device, _, self.access_token = redeem_pairing(
                pairing,
                device_code=device_code,
            )
        self.app = self.device.apps.get(app_key="gobii-desktop")

    def test_authenticated_socket_correlates_mcp_request_and_response(self):
        async def run():
            communicator = WebsocketCommunicator(
                ComputerRelayConsumer.as_asgi(),
                "/",
                headers=[
                    (b"authorization", f"Bearer {self.access_token}".encode("latin-1")),
                ],
                subprotocols=["gobii-computer-relay.v1"],
            )
            connected, subprotocol = await communicator.connect()
            self.assertTrue(connected)
            self.assertEqual(subprotocol, "gobii-computer-relay.v1")

            await communicator.send_json_to(
                {
                    "type": "hello",
                    "client_version": "0.21.0",
                    "protocol_version": 1,
                    "apps": _pairing_payload()["apps"],
                }
            )
            hello = await communicator.receive_json_from()
            self.assertEqual(hello["type"], "hello.ack")

            pending = asyncio.create_task(
                relay_mcp_request(
                    self.app.id,
                    {"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                    timeout_seconds=2,
                )
            )
            request = await communicator.receive_json_from()
            self.assertEqual(request["type"], "mcp.request")
            self.assertEqual(request["app"], "gobii-desktop")
            await communicator.send_json_to(
                {
                    "type": "mcp.response",
                    "request_id": request["request_id"],
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "result": {"tools": []},
                    },
                }
            )
            result = await pending
            self.assertEqual(result["result"], {"tools": []})

            await communicator.send_json_to({"type": "heartbeat"})
            heartbeat = await communicator.receive_json_from()
            self.assertEqual(heartbeat["type"], "heartbeat.ack")
            await communicator.disconnect()

        with patch("api.agent.tools.mcp_manager.get_mcp_manager"):
            async_to_sync(run)()

    def test_socket_is_not_present_until_hello_is_validated(self):
        async def run():
            communicator = WebsocketCommunicator(
                ComputerRelayConsumer.as_asgi(),
                "/",
                headers=[
                    (b"authorization", f"Bearer {self.access_token}".encode("latin-1")),
                ],
                subprotocols=["gobii-computer-relay.v1"],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            self.assertIsNone(get_device_presence(self.device.id))

            await communicator.send_json_to(
                {
                    "type": "hello",
                    "client_version": "0.21.0",
                    "protocol_version": 1,
                    "apps": _pairing_payload()["apps"],
                }
            )
            self.assertEqual((await communicator.receive_json_from())["type"], "hello.ack")
            self.assertIsNotNone(get_device_presence(self.device.id))
            await communicator.disconnect()

        with patch("api.agent.tools.mcp_manager.get_mcp_manager"):
            async_to_sync(run)()

    def test_timed_out_request_rejects_late_response(self):
        async def run():
            communicator = WebsocketCommunicator(
                ComputerRelayConsumer.as_asgi(),
                "/",
                headers=[
                    (b"authorization", f"Bearer {self.access_token}".encode("latin-1")),
                ],
                subprotocols=["gobii-computer-relay.v1"],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to(
                {
                    "type": "hello",
                    "client_version": "0.21.0",
                    "protocol_version": 1,
                    "apps": _pairing_payload()["apps"],
                }
            )
            await communicator.receive_json_from()

            pending = asyncio.create_task(
                relay_mcp_request(
                    self.app.id,
                    {"jsonrpc": "2.0", "id": 8, "method": "tools/list"},
                    timeout_seconds=1,
                )
            )
            request = await communicator.receive_json_from()
            with self.assertRaisesRegex(ComputerRelayError, "deadline_exceeded"):
                await pending
            await asyncio.sleep(0.01)
            await communicator.send_json_to(
                {
                    "type": "mcp.response",
                    "request_id": request["request_id"],
                    "payload": {"jsonrpc": "2.0", "id": 8, "result": {"tools": []}},
                }
            )
            late = await communicator.receive_json_from()
            self.assertEqual(late["error"]["code"], "unknown_request")
            await communicator.disconnect()

        with patch("api.agent.tools.mcp_manager.get_mcp_manager"):
            async_to_sync(run)()
