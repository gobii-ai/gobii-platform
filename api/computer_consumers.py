import json
import secrets

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from api.models import ComputerDevice
from api.services.computer_relay import (
    authenticate_relay_access_token,
    clear_device_presence,
    computer_client_version_supported,
    computer_relay_active_sockets,
    record_computer_relay_event,
    computer_cpp_enabled_for_user,
    set_device_presence,
    sync_device_manifest,
)


class ComputerRelayConsumer(AsyncJsonWebsocketConsumer):
    subprotocol = "gobii-computer-relay.v1"

    async def connect(self):
        offered = self.scope.get("subprotocols") or []
        if self.subprotocol not in offered:
            await self.close(code=4406)
            return
        token = self._bearer_token()
        if not token:
            await self.close(code=4401)
            return
        try:
            self.device = await sync_to_async(
                authenticate_relay_access_token,
                thread_sensitive=True,
            )(token)
        except PermissionError:
            await self.close(code=4403)
            return

        self.connection_generation = secrets.token_urlsafe(18)
        self.pending_replies: dict[str, str] = {}
        self.received_hello = False
        from api.services.computer_relay import get_device_presence

        previous = get_device_presence(self.device.id)
        if previous:
            await self.channel_layer.send(
                previous["channel"],
                {
                    "type": "computer.control",
                    "event_type": "relay.close",
                    "payload": {"code": "superseded"},
                },
            )
        set_device_presence(
            self.device.id,
            channel_name=self.channel_name,
            generation=self.connection_generation,
        )
        self.socket_counted = True
        computer_relay_active_sockets.add(1, {"platform": self.device.platform})
        record_computer_relay_event("socket_connected", platform=self.device.platform)
        await self.accept(subprotocol=self.subprotocol)

    def _bearer_token(self) -> str:
        for name, value in self.scope.get("headers") or []:
            if name.lower() != b"authorization":
                continue
            authorization = value.decode("latin-1")
            if authorization.lower().startswith("bearer "):
                return authorization.split(" ", 1)[1].strip()
        return ""

    async def disconnect(self, close_code):
        if hasattr(self, "device"):
            clear_device_presence(self.device.id, getattr(self, "connection_generation", ""))
            if getattr(self, "socket_counted", False):
                computer_relay_active_sockets.add(-1, {"platform": self.device.platform})
                record_computer_relay_event("socket_disconnected", platform=self.device.platform)
        for request_id, reply_channel in getattr(self, "pending_replies", {}).items():
            await self.channel_layer.send(
                reply_channel,
                {
                    "type": "computer.mcp_response",
                    "request_id": request_id,
                    "error": {
                        "code": "offline",
                        "message": "Computer disconnected before responding",
                        "retryable": True,
                    },
                },
            )

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if bytes_data is not None or text_data is None:
            await self.close(code=4400)
            return
        if len(text_data.encode("utf-8")) > settings.COMPUTER_CPP_MAX_FRAME_BYTES:
            await self.close(code=4409)
            return
        try:
            content = json.loads(text_data)
        except json.JSONDecodeError:
            await self.close(code=4400)
            return
        if not isinstance(content, dict):
            await self.close(code=4400)
            return
        await self.receive_json(content)

    async def receive_json(self, content, **kwargs):
        message_type = str(content.get("type") or "")
        if not self.received_hello and message_type != "hello":
            await self.close(code=4400)
            return
        if message_type == "hello":
            await self._handle_hello(content)
        elif message_type == "heartbeat":
            await self._handle_heartbeat()
        elif message_type == "mcp.response":
            await self._handle_mcp_response(content)
        else:
            await self.send_json(
                {"type": "error", "error": {"code": "invalid_message", "message": "Unknown message type"}}
            )

    async def _handle_hello(self, content):
        if int(content.get("protocol_version") or 0) != settings.COMPUTER_CPP_RELAY_PROTOCOL_VERSION:
            await self.send_json(
                {
                    "type": "error",
                    "error": {"code": "update_required", "message": "Relay protocol update required"},
                }
            )
            await self.close(code=4406)
            return
        client_version = str(content.get("client_version") or "")
        if not computer_client_version_supported(client_version):
            record_computer_relay_event(
                "client_version_rejected",
                platform=self.device.platform,
            )
            await self.send_json(
                {
                    "type": "error",
                    "error": {"code": "update_required", "message": "Computer application update required"},
                }
            )
            await self.close(code=4406)
            return
        apps = content.get("apps", [])
        try:
            await sync_to_async(sync_device_manifest, thread_sensitive=True)(self.device, apps)
        except ValueError as exc:
            await self.send_json(
                {"type": "error", "error": {"code": "invalid_manifest", "message": str(exc)}}
            )
            await self.close(code=4400)
            return

        self.device.client_version = client_version[:32]
        self.device.protocol_version = int(content.get("protocol_version"))
        self.device.last_seen_at = timezone.now()
        await sync_to_async(self.device.save, thread_sensitive=True)(
            update_fields=["client_version", "protocol_version", "last_seen_at", "updated_at"]
        )
        self.received_hello = True
        set_device_presence(
            self.device.id,
            channel_name=self.channel_name,
            generation=self.connection_generation,
        )
        await self.send_json(
            {
                "type": "hello.ack",
                "device_id": str(self.device.id),
                "heartbeat_interval": 20,
                "max_frame_bytes": settings.COMPUTER_CPP_MAX_FRAME_BYTES,
            }
        )

    async def _handle_heartbeat(self):
        enabled = await sync_to_async(computer_cpp_enabled_for_user, thread_sensitive=True)(
            self.device.owner
        )
        if not enabled:
            await self.close(code=4403)
            return
        set_device_presence(
            self.device.id,
            channel_name=self.channel_name,
            generation=self.connection_generation,
        )
        now = timezone.now()
        if self.device.last_seen_at is None or (now - self.device.last_seen_at).total_seconds() >= 60:
            self.device.last_seen_at = now
            await sync_to_async(
                ComputerDevice.objects.filter(id=self.device.id).update,
                thread_sensitive=True,
            )(last_seen_at=now)
        await self.send_json({"type": "heartbeat.ack"})

    async def _handle_mcp_response(self, content):
        request_id = str(content.get("request_id") or "")
        reply_channel = self.pending_replies.pop(request_id, None)
        if not reply_channel:
            await self.send_json(
                {"type": "error", "error": {"code": "unknown_request", "message": "Unknown request ID"}}
            )
            return
        payload = content.get("payload")
        error = content.get("error")
        await self.channel_layer.send(
            reply_channel,
            {
                "type": "computer.mcp_response",
                "request_id": request_id,
                "payload": payload,
                "error": error,
            },
        )

    async def computer_mcp_request(self, event):
        if event.get("connection_generation") != self.connection_generation:
            return
        request_id = str(event["request_id"])
        self.pending_replies[request_id] = str(event["reply_channel"])
        message = {
            "type": "mcp.request",
            "request_id": request_id,
            "app": event["app"],
            "deadline_ms": event["deadline_ms"],
            "payload": event["payload"],
        }
        if len(json.dumps(message, separators=(",", ":")).encode("utf-8")) > settings.COMPUTER_CPP_MAX_FRAME_BYTES:
            self.pending_replies.pop(request_id, None)
            await self.channel_layer.send(
                event["reply_channel"],
                {
                    "type": "computer.mcp_response",
                    "request_id": request_id,
                    "error": {
                        "code": "payload_too_large",
                        "message": "Computer request exceeds the relay frame limit",
                    },
                },
            )
            return
        await self.send_json(message)

    async def computer_control(self, event):
        await self.send_json({"type": event["event_type"], **event.get("payload", {})})
        if event["event_type"] == "relay.close":
            await self.close(code=4403)
