import base64
import json
import random
import string

from django.test import SimpleTestCase, tag

from ..json_goldilocks import goldilocks_summary, json_goldilocks


_TEXT_ALPHABET = string.ascii_letters + string.digits + " _-/:,.;[](){}"


def _rand_token(rng: random.Random, min_len: int = 1, max_len: int = 12) -> str:
    length = rng.randint(min_len, max_len)
    return "".join(rng.choice(_TEXT_ALPHABET) for _ in range(length))


def _rand_log(rng: random.Random) -> str:
    lines = []
    for i in range(rng.randint(8, 40)):
        level = rng.choice(["INFO", "WARN", "ERROR", "DEBUG"])
        lines.append(f"2024-01-01 12:00:{i:02d} {level} line {i}")
    return "\n".join(lines)


def _rand_stack_trace(rng: random.Random) -> str:
    lines = ["Traceback (most recent call last):"]
    for i in range(rng.randint(10, 35)):
        lines.append(f'File "app.py", line {i}, in fn_{i}')
    lines.append("ValueError: boom")
    return "\n".join(lines)


def _rand_sql(rng: random.Random) -> str:
    clauses = " OR ".join(f"col{i}=1" for i in range(rng.randint(10, 80)))
    return f"SELECT * FROM table WHERE {clauses}"


def _rand_messy_string(rng: random.Random) -> str:
    choice = rng.randint(0, 8)
    if choice == 0:
        return f"<div><span>{_rand_token(rng, 5, 40)}</span></div>"
    if choice == 1:
        return "# Header\n\n- item\n\n```code```"
    if choice == 2:
        return _rand_log(rng)
    if choice == 3:
        return _rand_stack_trace(rng)
    if choice == 4:
        return _rand_sql(rng)
    if choice == 5:
        return "a=1&b=two%20words&c=%7B%22id%22%3A1%7D"
    if choice == 6:
        raw = json.dumps({"id": rng.randint(1, 999), "name": _rand_token(rng, 3, 12)})
        return base64.b64encode(raw.encode("utf-8")).decode("ascii")
    if choice == 7:
        payload = {"id": rng.randint(1, 999), "name": _rand_token(rng, 3, 12)}
        return json.dumps(payload)
    return _rand_token(rng, 50, 400)


def _rand_value(rng: random.Random, depth: int = 0, max_depth: int = 4):
    if depth >= max_depth:
        return rng.choice([None, rng.randint(-1000, 1000), rng.random(), _rand_messy_string(rng)])

    roll = rng.random()
    if roll < 0.4:
        return rng.choice([None, rng.randint(-1000, 1000), rng.random(), _rand_messy_string(rng)])
    if roll < 0.7:
        return [_rand_value(rng, depth + 1, max_depth) for _ in range(rng.randint(0, 6))]

    keys = [
        "data", "items", "payload", "start", "meta", "results",
        "content", "body", "message", "log", "stacktrace", "query",
        "error", "status", "id", "name", "details",
    ]
    payload = {}
    for _ in range(rng.randint(0, 6)):
        key = rng.choice(keys + [_rand_token(rng, 3, 10)])
        if key in {"payload", "data", "start"} and rng.random() < 0.4:
            payload[key] = _rand_messy_string(rng)
        else:
            payload[key] = _rand_value(rng, depth + 1, max_depth)
    return payload


@tag("context_hints_batch")
class JsonGoldilocksTests(SimpleTestCase):
    """Tests for messy JSON focus extraction."""

    def test_summary_caps_bytes(self):
        payload = {
            "data": {
                "items": [
                    {"id": i, "name": f"Item {i}", "description": "x" * 200}
                    for i in range(60)
                ]
            }
        }
        summary = goldilocks_summary(payload, max_bytes=500)

        self.assertLessEqual(len(summary.encode("utf-8")), 500)
        self.assertIn("ARRAY_TOTAL", summary)

    def test_redacts_secrets(self):
        payload = {
            "password": "supersecretpassword",
            "token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        }
        summary = goldilocks_summary(payload, max_bytes=500)

        self.assertIn("REDACTED", summary)

    def test_embedded_json_string(self):
        payload = {
            "payload": json.dumps({"id": 1, "name": "Alice", "status": "active"})
        }
        summary = goldilocks_summary(payload, max_bytes=500)

        self.assertIn("[EMBEDDED_JSON]", summary)
        self.assertIn("Alice", summary)

    def test_html_string_stripped(self):
        payload = {"content": "<div>Hello <b>World</b></div>"}
        summary = goldilocks_summary(payload, max_bytes=300)

        self.assertIn("Hello", summary)
        self.assertNotIn("<div>", summary)

    def test_root_list_supported(self):
        payload = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        summary = goldilocks_summary(payload, max_bytes=300)

        self.assertIn("One", summary)
        self.assertIn("Two", summary)

    def test_max_depth_marker(self):
        deep = {}
        current = deep
        for _ in range(12):
            current["data"] = {}
            current = current["data"]
        result = json_goldilocks(deep, max_depth=2)
        summary = json.dumps(result, ensure_ascii=False)

        self.assertIn("MAX_DEPTH_REACHED", summary)

    def test_json_in_json_in_json(self):
        payload = {
            "outer": json.dumps({
                "inner": json.dumps({"id": 7, "name": "Nested", "status": "ok"})
            })
        }
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertIn("Nested", summary)
        self.assertIn("[EMBEDDED_JSON]", summary)

    def test_base64_json_in_json(self):
        raw = json.dumps({"id": 1, "name": "Base", "notes": "x" * 80})
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        payload = {"data": encoded}
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertIn("BASE64_JSON", summary)
        self.assertIn("Base", summary)

    def test_base64_binary_in_json(self):
        raw = b"\x00\x01\x02\x03" * 40
        encoded = base64.b64encode(raw).decode("ascii")
        payload = {"blob": encoded}
        summary = goldilocks_summary(payload, max_bytes=500)

        self.assertIn("BASE64", summary)

    def test_markdown_in_json_in_json(self):
        payload = {
            "payload": json.dumps({
                "content": "# Header\n\n- item\n\n```code```"
            })
        }
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertIn("Header", summary)

    def test_escaped_json_under_start_key(self):
        payload = {"start": json.dumps({"id": 99, "title": "Start Here"})}
        summary = goldilocks_summary(payload, max_bytes=600)

        self.assertIn("Start Here", summary)

    def test_urlencoded_payload(self):
        payload = {
            "request_body": "a=1&b=two%20words&c=%7B%22id%22%3A1%7D"
        }
        summary = goldilocks_summary(payload, max_bytes=600)

        self.assertIn("URL_ENCODED", summary)
        self.assertIn("a=1", summary)

    def test_stack_trace_processing(self):
        stack_lines = [
            "Traceback (most recent call last):",
        ]
        for i in range(30):
            stack_lines.append(f'File "app.py", line {i}, in fn_{i}')
        stack_lines.append("ValueError: boom")
        payload = {"stacktrace": "\n".join(stack_lines)}
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertIn("frames omitted", summary)

    def test_log_processing(self):
        log_lines = [f"2024-01-01 12:00:{i:02d} INFO line {i}" for i in range(40)]
        payload = {"log": "\n".join(log_lines)}
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertIn("Total:", summary)

    def test_sql_processing(self):
        query = (
            "SELECT * FROM table WHERE " + " OR ".join([f"col{i}=1" for i in range(80)])
        )
        payload = {"query": query}
        summary = goldilocks_summary(payload, max_bytes=300)

        self.assertIn("query truncated", summary)

    def test_bloat_keys_skipped(self):
        payload = {
            "title": "Keep Me",
            "html": "<html><body>Ignore</body></html>",
            "body_html": "<p>Ignore</p>",
        }
        summary = goldilocks_summary(payload, max_bytes=400)

        self.assertIn("Keep Me", summary)
        self.assertNotIn("<html", summary)
        self.assertNotIn("body_html", summary)

    def test_container_detection_nested(self):
        payload = {
            "meta": {"page": 1, "total": 2},
            "data": {"items": [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]},
            "results": [{"id": 3, "name": "Gamma"}],
        }
        summary = goldilocks_summary(payload, max_bytes=800)

        self.assertTrue("Alpha" in summary or "Gamma" in summary)
        self.assertIn("total", summary.lower())

    def test_heterogeneous_array(self):
        items = ([{"id": 1}, "text", 3, {"id": 2}] * 4) + [{"id": 99}]
        payload = {"items": items}
        summary = goldilocks_summary(payload, max_bytes=500)

        self.assertIn("ARRAY_TOTAL", summary)

    def test_noise_string_detected(self):
        noise = ("Aa1!Bb2@Cc3#Dd4$Ee5%Ff6^" * 10) + "!"
        payload = {"content": noise}
        summary = goldilocks_summary(payload, max_bytes=400)

        self.assertIn("NOISE_DATA", summary)

    def test_fuzz_random_payloads_are_capped(self):
        rng = random.Random(1337)
        for _ in range(60):
            payload = _rand_value(rng, depth=0, max_depth=4)
            summary = goldilocks_summary(payload, max_bytes=800)

            self.assertIsInstance(summary, str)
            self.assertLessEqual(len(summary.encode("utf-8")), 800)
            self.assertTrue(summary)
