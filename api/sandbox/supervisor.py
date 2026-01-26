import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SandboxSupervisorHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/exec":
            self._send(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return

        command = data.get("command")
        if not command:
            self._send(400, {"error": "command_required"})
            return

        if isinstance(command, list):
            cmd = [str(part) for part in command]
            shell = False
        else:
            cmd = str(command)
            shell = True

        cwd = data.get("cwd") or os.getenv("SANDBOX_WORKDIR") or "/workspace"
        env = os.environ.copy()
        if isinstance(data.get("env"), dict):
            env.update({str(k): str(v) for k, v in data["env"].items()})

        timeout = data.get("timeout")
        try:
            timeout_seconds = int(timeout) if timeout is not None else 30
        except ValueError:
            timeout_seconds = 30
        timeout_seconds = max(1, min(timeout_seconds, 120))

        try:
            completed = subprocess.run(
                cmd,
                shell=shell,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._send(408, {"error": "timeout"})
            return

        self._send(
            200,
            {
                "exit_code": completed.returncode,
                "stdout": completed.stdout or "",
                "stderr": completed.stderr or "",
            },
        )



def run():
    port = int(os.getenv("SANDBOX_SUPERVISOR_PORT", "8081"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SandboxSupervisorHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
