import asyncio

from asgiref.sync import ThreadSensitiveContext
from asgiref.wsgi import WsgiToAsgi

from sandbox_server.app import application as wsgi_application
from sandbox_server.server.sqlite_rsync import (
    SQLITE_RSYNC_WEBSOCKET_PATH,
    websocket_application,
)

http_application = WsgiToAsgi(wsgi_application)
_HTTP_CONCURRENCY = asyncio.Semaphore(4)


async def application(scope, receive, send):
    if scope["type"] == "websocket":
        if scope.get("path", "").rstrip("/") == SQLITE_RSYNC_WEBSOCKET_PATH:
            await websocket_application(scope, receive, send)
            return
        await send({"type": "websocket.close", "code": 4404})
        return
    # WsgiToAsgi otherwise shares one thread-sensitive executor for the whole
    # process, which would make health checks wait behind long-running tools.
    async with _HTTP_CONCURRENCY, ThreadSensitiveContext():
        await http_application(scope, receive, send)
