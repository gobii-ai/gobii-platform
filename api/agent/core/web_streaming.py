import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from api.models import CommsChannel, PersistentAgent, PersistentAgentMessage, parse_web_user_address


@dataclass(frozen=True)
class WebStreamTarget:
    agent_id: str
    user_id: int
    address: str


def resolve_web_stream_target(agent: PersistentAgent) -> Optional[WebStreamTarget]:
    """Return the active web UI target if the agent's latest outbound message was web."""
    last_outbound = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=True)
        .select_related("to_endpoint")
        .order_by("-timestamp", "-seq")
        .first()
    )
    if not last_outbound or not last_outbound.to_endpoint:
        return None
    if last_outbound.to_endpoint.channel != CommsChannel.WEB:
        return None

    latest_message = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint")
        .order_by("-timestamp", "-seq")
        .first()
    )
    if not latest_message or not latest_message.from_endpoint:
        return None
    if latest_message.from_endpoint.channel != CommsChannel.WEB:
        return None

    if latest_message.is_outbound:
        if not latest_message.to_endpoint:
            return None
        address = latest_message.to_endpoint.address
    else:
        address = latest_message.from_endpoint.address

    user_id, agent_id = parse_web_user_address(address)
    if user_id is None or agent_id != str(agent.id):
        return None

    return WebStreamTarget(agent_id=str(agent.id), user_id=user_id, address=address)


@dataclass
class WebStreamBroadcaster:
    target: WebStreamTarget
    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    min_flush_interval: float = 0.08
    max_buffer_chars: int = 200
    _last_flush: float = field(default=0.0, init=False)
    _started: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _reasoning_buffer: list[str] = field(default_factory=list, init=False)
    _content_buffer: list[str] = field(default_factory=list, init=False)

    def start(self) -> None:
        if self._started or self._finished:
            return
        self._started = True
        self._last_flush = time.monotonic()
        self._send({"stream_id": self.stream_id, "status": "start"})

    def push_delta(self, reasoning_delta: Optional[str], content_delta: Optional[str]) -> None:
        if self._finished:
            return
        if not self._started:
            self.start()
        if reasoning_delta:
            self._reasoning_buffer.append(reasoning_delta)
        if content_delta:
            self._content_buffer.append(content_delta)
        if self._should_flush():
            self.flush()

    def flush(self) -> None:
        if self._finished or not self._started:
            return
        if not self._reasoning_buffer and not self._content_buffer:
            return

        payload = {"stream_id": self.stream_id, "status": "delta"}
        if self._reasoning_buffer:
            payload["reasoning_delta"] = "".join(self._reasoning_buffer)
        if self._content_buffer:
            payload["content_delta"] = "".join(self._content_buffer)

        self._reasoning_buffer = []
        self._content_buffer = []
        self._last_flush = time.monotonic()
        self._send(payload)

    def finish(self) -> None:
        if self._finished:
            return
        if self._started:
            self.flush()
            self._send({"stream_id": self.stream_id, "status": "done"})
        self._finished = True

    def _should_flush(self) -> bool:
        if not self._reasoning_buffer and not self._content_buffer:
            return False
        buffered_chars = sum(len(part) for part in self._reasoning_buffer) + sum(len(part) for part in self._content_buffer)
        if buffered_chars >= self.max_buffer_chars:
            return True
        return (time.monotonic() - self._last_flush) >= self.min_flush_interval

    def _send(self, payload: dict) -> None:
        from console.agent_chat.realtime import send_stream_event

        send_stream_event(self.target.agent_id, self.target.user_id, payload)
