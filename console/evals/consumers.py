import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class _BaseEvalConsumer(AsyncJsonWebsocketConsumer):
    group_name: str | None = None

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
            await self.close(code=4403)
            return

        if not self.group_name or self.channel_layer is None:
            await self.close(code=1011)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group_name and self.channel_layer is not None:
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Failed to discard channel from eval group %s: %s", self.group_name, exc)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def suite_update(self, event):
        await self.send_json({"type": "suite.update", "payload": event.get("payload")})

    async def run_update(self, event):
        await self.send_json({"type": "run.update", "payload": event.get("payload")})

    async def task_update(self, event):
        await self.send_json({"type": "task.update", "payload": event.get("payload")})


class EvalSuiteRunConsumer(_BaseEvalConsumer):
    async def connect(self):
        suite_run_id = self.scope.get("url_route", {}).get("kwargs", {}).get("suite_run_id")
        self.group_name = f"eval-suite-{suite_run_id}"
        await super().connect()


class EvalRunConsumer(_BaseEvalConsumer):
    async def connect(self):
        run_id = self.scope.get("url_route", {}).get("kwargs", {}).get("run_id")
        self.group_name = f"eval-run-{run_id}"
        await super().connect()
