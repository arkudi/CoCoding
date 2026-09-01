from types import SimpleNamespace

import pytest

from app.agent.provider import DeepSeekClient, ModelProtocolError, ModelProviderError
from app.agent.types import AssistantTurn, ToolCall


def _content_chunk(content):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_chunk(index, *, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    fragment = SimpleNamespace(index=index, id=call_id, function=function)
    delta = SimpleNamespace(content=None, reasoning_content="private", tool_calls=[fragment])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _client_returning(stream):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: stream))
    )


def _client_with_create(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_complete_streams_visible_content_and_reconstructs_turn():
    captured = {}
    stream = iter([_content_chunk("Hello"), _content_chunk(None), _content_chunk(" world")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: captured.update(kwargs) or stream
    )))
    deltas = []

    turn = DeepSeekClient(client, "deepseek-v4-flash").complete(
        [{"role": "user", "content": "hello"}],
        [{"type": "function"}],
        on_text_delta=deltas.append,
    )

    assert captured["stream"] is True
    assert deltas == ["Hello", " world"]
    assert turn == AssistantTurn("Hello world")


def test_complete_reconstructs_tools_without_emitting_reasoning():
    """Replacing assembly with only the last fragment must fail this test."""
    stream = iter(
        [
            _tool_chunk(0, call_id="call_1", name="write_file", arguments='{"pa'),
            _tool_chunk(0, arguments='th":"a.txt","content":"ok"}'),
        ]
    )
    deltas = []

    turn = DeepSeekClient(_client_returning(stream), "deepseek-v4-flash").complete(
        [], [], on_text_delta=deltas.append
    )

    assert deltas == []
    assert turn.tool_calls == (
        ToolCall("call_1", "write_file", '{"path":"a.txt","content":"ok"}'),
    )


def test_complete_reconstructs_interleaved_tools_in_index_order():
    """Using arrival order instead of the fragment index must fail this test."""
    stream = iter(
        [
            _tool_chunk(1, call_id="call_2", name="read_", arguments='{"pa'),
            _tool_chunk(0, call_id="call_1", name="write_", arguments='{"pa'),
            _tool_chunk(1, name="file", arguments='th":"b.txt"}'),
            _tool_chunk(0, name="file", arguments='th":"a.txt"}'),
        ]
    )

    turn = DeepSeekClient(_client_returning(stream), "deepseek-v4-flash").complete([], [])

    assert turn.tool_calls == (
        ToolCall("call_1", "write_file", '{"path":"a.txt"}'),
        ToolCall("call_2", "read_file", '{"path":"b.txt"}'),
    )


def test_complete_rejects_tool_stream_without_id_or_name():
    """Returning a ToolCall for incomplete streamed metadata must fail this test."""
    stream = iter([_tool_chunk(0, arguments='{"path":"a.txt"}')])

    with pytest.raises(ModelProtocolError) as captured:
        DeepSeekClient(_client_returning(stream), "deepseek-v4-flash").complete([], [])

    assert captured.value.code == "protocol_error"


def test_complete_does_not_retry_after_visible_output(monkeypatch):
    """Retrying after delivering a visible partial response must fail this test."""
    import app.agent.provider as provider

    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1

        def broken_stream():
            yield _content_chunk("partial")
            raise _connection_error()

        return broken_stream()

    monkeypatch.setattr(provider.time, "sleep", lambda delay: None)
    deltas = []
    with pytest.raises(ModelProviderError) as captured:
        DeepSeekClient(_client_with_create(create), "deepseek-v4-flash").complete(
            [], [], on_text_delta=deltas.append
        )

    assert deltas == ["partial"]
    assert calls == 1
    assert captured.value.code == "provider_unavailable"


def test_complete_does_not_retry_after_visible_output_on_iterator_5xx(monkeypatch):
    """Retrying an iterator-raised 5xx after visible text must fail this test."""
    import app.agent.provider as provider

    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1

        def broken_stream():
            yield _content_chunk("partial")
            raise _server_error()

        return broken_stream()

    monkeypatch.setattr(provider.time, "sleep", lambda delay: None)
    deltas = []
    with pytest.raises(ModelProviderError) as captured:
        DeepSeekClient(_client_with_create(create), "deepseek-v4-flash").complete(
            [], [], on_text_delta=deltas.append
        )

    assert deltas == ["partial"]
    assert calls == 1
    assert captured.value.code == "provider_unavailable"


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

    adapter = DeepSeekClient.from_settings(settings)

    assert captured == {
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
        "max_retries": 0,
    }
    assert adapter._model == "deepseek-v4-flash"


def test_complete_disables_thinking_and_converts_tool_calls():
    """Removing native function-tool arguments from a response must fail this test."""
    captured = {}
    stream = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
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
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: captured.update(kwargs) or stream
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
            completions=SimpleNamespace(create=lambda **kwargs: iter([SimpleNamespace(choices=[])]))
        )
    )

    with pytest.raises(ModelProtocolError) as captured:
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])

    assert captured.value.code == "protocol_error"
    assert captured.value.safe_message == "The model provider returned an invalid response."
    assert "choices" not in str(captured.value).casefold()


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
    raised_errors = []

    def create(**kwargs):
        nonlocal calls
        calls += 1
        error = error_factory()
        raised_errors.append(error)
        raise error

    monkeypatch.setattr(provider.time, "sleep", lambda delay: None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with pytest.raises(ModelProviderError) as captured:
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])

    assert calls == 3
    assert captured.value.code == "provider_unavailable"
    assert captured.value.safe_message == "The model provider is temporarily unavailable."
    assert str(raised_errors[-1]) not in str(captured.value)


@pytest.mark.parametrize(
    ("error_factory", "expected_code", "expected_message", "raw_text"),
    [
        pytest.param(
            lambda: _authentication_error(),
            "authentication_failed",
            "The model provider credentials are invalid.",
            "invalid key",
            id="authentication",
        ),
        pytest.param(
            lambda: _invalid_request_error(),
            "invalid_request",
            "The model provider request is invalid.",
            "invalid request",
            id="invalid-request",
        ),
    ],
)
def test_complete_does_not_retry_non_transient_errors(
    error_factory, expected_code, expected_message, raw_text
):
    """Retrying credentials or malformed requests must fail this test."""
    calls = 0

    def create(**kwargs):
        nonlocal calls
        calls += 1
        raise error_factory()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with pytest.raises(ModelProviderError) as captured:
        DeepSeekClient(client=client, model="deepseek-v4-flash").complete([], [])

    assert calls == 1
    assert captured.value.code == expected_code
    assert captured.value.safe_message == expected_message
    assert raw_text not in str(captured.value)


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
