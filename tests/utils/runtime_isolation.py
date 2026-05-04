import ipaddress
import os
import socket
from types import SimpleNamespace

_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex

_MOCK_LITELLM_RESPONSES = {
    "json array containing exactly three short tags": '["Operations", "Research", "Support"]',
    "plain-language sentence under 160 characters": "Test agent summary.",
    "physical identity": "A friendly professional with an approachable expression.",
    "visual identities": "A friendly professional with an approachable expression.",
}


def _test_socket_host_is_local(host):
    if host in ("", None):
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    host = str(host).strip().lower().rstrip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _assert_test_socket_address_allowed(address):
    if not isinstance(address, tuple) or not address:
        return
    host = address[0]
    if _test_socket_host_is_local(host):
        return
    raise RuntimeError(
        "Live network access is disabled in tests: attempted connection to "
        f"{host!r}. Mock the provider/client, or set "
        "GOBII_ALLOW_LIVE_TEST_NETWORK=1 for intentional integration tests."
    )


def _guarded_test_socket_connect(self, address):
    _assert_test_socket_address_allowed(address)
    return _ORIGINAL_SOCKET_CONNECT(self, address)


def _guarded_test_socket_connect_ex(self, address):
    _assert_test_socket_address_allowed(address)
    return _ORIGINAL_SOCKET_CONNECT_EX(self, address)


class _TestLiteLLMResponse(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)


def _test_litellm_content(messages):
    combined = "\n".join(
        str(message.get("content", ""))
        for message in messages or []
        if isinstance(message, dict)
    ).lower()

    for trigger, response in _MOCK_LITELLM_RESPONSES.items():
        if trigger in combined:
            return response

    return "Test completion."


def _test_litellm_usage():
    details = SimpleNamespace(cached_tokens=0)
    return SimpleNamespace(
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        prompt_tokens_details=details,
    )


def _test_litellm_response(**kwargs):
    usage = _test_litellm_usage()
    content = _test_litellm_content(kwargs.get("messages"))
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=[],
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop", index=0)
    return _TestLiteLLMResponse(
        id="test-litellm-completion",
        response_id="test-litellm-completion",
        choices=[choice],
        usage=usage,
        model=kwargs.get("model"),
        provider=kwargs.get("custom_llm_provider") or kwargs.get("provider"),
        model_extra={"usage": usage},
    )


def _test_litellm_stream(**kwargs):
    usage = _test_litellm_usage()
    content = _test_litellm_content(kwargs.get("messages"))
    return iter(
        [
            SimpleNamespace(
                id="test-litellm-completion",
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=content, reasoning_content=None, tool_calls=None),
                        finish_reason=None,
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                id="test-litellm-completion",
                choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")],
                usage=usage,
            ),
        ]
    )


def _test_litellm_completion(**kwargs):
    if kwargs.get("stream"):
        return _test_litellm_stream(**kwargs)
    return _test_litellm_response(**kwargs)


def install_test_runtime_isolation():
    if os.environ.get("GOBII_ALLOW_LIVE_TEST_NETWORK") != "1":
        socket.socket.connect = _guarded_test_socket_connect
        socket.socket.connect_ex = _guarded_test_socket_connect_ex

    if os.environ.get("GOBII_ALLOW_LIVE_TEST_LLM") != "1":
        import litellm

        litellm.completion = _test_litellm_completion
