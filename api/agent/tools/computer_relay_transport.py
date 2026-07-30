import contextlib
from typing import AsyncIterator

import anyio
from asgiref.sync import sync_to_async
from fastmcp.client.transports import ClientTransport
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from api.services.computer_relay import (
    ComputerRelayError,
    consume_artifact,
    relay_mcp_request,
)


async def _resolve_artifact_markers(value, device_id):
    if isinstance(value, list):
        return [await _resolve_artifact_markers(item, device_id) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get("_gobii_artifact")
    if isinstance(marker, dict) and marker.get("id"):
        return await sync_to_async(consume_artifact, thread_sensitive=True)(
            device_id,
            marker["id"],
        )
    return {
        key: await _resolve_artifact_markers(item, device_id)
        for key, item in value.items()
    }


class ComputerRelayTransport(ClientTransport):
    def __init__(self, *, device_app_id: str, device_id: str):
        self.device_app_id = device_app_id
        self.device_id = device_id

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs) -> AsyncIterator[ClientSession]:
        server_send, client_receive = anyio.create_memory_object_stream[SessionMessage | Exception](16)
        client_send, server_receive = anyio.create_memory_object_stream[SessionMessage](16)

        async def bridge():
            async with server_receive, server_send:
                async for session_message in server_receive:
                    request_payload = session_message.message.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                    try:
                        response_payload = await relay_mcp_request(
                            self.device_app_id,
                            request_payload,
                        )
                        if not response_payload:
                            continue
                        response_payload = await _resolve_artifact_markers(
                            response_payload,
                            self.device_id,
                        )
                        parsed = JSONRPCMessage.model_validate(response_payload)
                        await server_send.send(SessionMessage(parsed))
                    except ComputerRelayError as exc:
                        await server_send.send(exc)
                        return
                    except (TypeError, ValueError):
                        await server_send.send(
                            ComputerRelayError(
                                "invalid_response",
                                "Computer returned an invalid MCP response",
                            )
                        )
                        return

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(bridge)
            async with ClientSession(
                client_receive,
                client_send,
                **session_kwargs,
            ) as session:
                try:
                    yield session
                finally:
                    task_group.cancel_scope.cancel()
