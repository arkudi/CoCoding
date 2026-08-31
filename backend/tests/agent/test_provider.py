from types import SimpleNamespace

import pytest

from app.agent.provider import DeepSeekClient, ModelProtocolError


def test_from_settings_disables_sdk_retries(monkeypatch):
    """Allowing SDK retries would exceed the adapter's three-attempt contract."""
    import app.agent.provider as provider

    captured = {}

    class OpenAIStub:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(provider, "OpenAI", OpenAIStub)
    settings = SimpleNamespace(
        deepseek_api_key="test-key",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-flash",
    )

    DeepSeekClient.from_settings(settings)

    assert captured["max_retries"] == 0


def test_complete_disables_thinking_and_converts_tool_calls():
    """Removing native function-tool arguments from a response must fail this test."""
    captured = {}
    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="read_file", arguments='{"path":"a.py"}'
                            ),
                        )
                    ],
                )
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: captured.update(kwargs) or completion
            )
        )
    )

    turn = DeepSeekClient(client=client, model="deepseek-v4-flash").complete(
        [{"role": "user", "content": "inspect"}], [{"type": "function"}]
    )

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["tool_choice"] == "auto"
    assert captured["model"] == "deepseek-v4-flash"
    assert turn.content is None
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments_json == '{"path":"a.py"}'


def test_complete_rejects_response_without_a_choice():
    """Silently accepting a malformed provider response must fail this test."""
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(choices=[]))
        )
    )

    with pytest.raises(ModelProtocolError):
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(lambda: _rate_limit_error(), id="rate-limit"),
        pytest.param(lambda: _connection_error(), id="connection"),
        pytest.param(lambda: _timeout_error(), id="timeout"),
        pytest.param(lambda: _server_error(), id="server-5xx"),
    ],
)
def test_complete_retries_transient_errors_three_times(monkeypatch, error_factory):
    """Reducing transient failures to fewer than three total calls must fail this test."""
    import app.agent.provider as provider

    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1
        raise error_factory()

    monkeypatch.setattr(provider.time, "sleep", lambda delay: None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with pytest.raises(Exception):
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])

    assert calls == 3


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(lambda: _authentication_error(), id="authentication"),
        pytest.param(lambda: _invalid_request_error(), id="invalid-request"),
    ],
)
def test_complete_does_not_retry_non_transient_errors(error_factory):
    """Retrying credentials or malformed requests must fail this test."""
    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1
        raise error_factory()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with pytest.raises(Exception):
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])

    assert calls == 1


def _request():
    from httpx import Request

    return Request("POST", "https://api.deepseek.com/chat/completions")


def _rate_limit_error():
    from openai import RateLimitError
    from httpx import Response

    return RateLimitError("rate limited", response=Response(429, request=_request()), body=None)


def _connection_error():
    from openai import APIConnectionError

    return APIConnectionError(request=_request())


def _timeout_error():
    from openai import APITimeoutError

    return APITimeoutError(request=_request())


def _server_error():
    from openai import APIStatusError
    from httpx import Response

    return APIStatusError("server error", response=Response(503, request=_request()), body=None)


def _authentication_error():
    from openai import AuthenticationError
    from httpx import Response

    return AuthenticationError("invalid key", response=Response(401, request=_request()), body=None)


def _invalid_request_error():
    from openai import BadRequestError
    from httpx import Response

    return BadRequestError("invalid request", response=Response(400, request=_request()), body=None)
