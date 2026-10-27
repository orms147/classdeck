from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import openai
import pytest

try:  # openai 3.x ships its own copy of httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover - older SDKs
    import httpx

from classdeck.llm import LLMError, Message, OpenAICompatibleProvider

HI = [Message(role="user", content="hi")]
URL = "https://api.example.test/v1/chat/completions"


def _make_status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    body = {"error": {"message": f"status {status}"}}
    response = httpx.Response(status, json=body, request=httpx.Request("POST", URL))
    return cls(f"Error code: {status}", response=response, body=body)


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", URL))


def _completion(
    text: str | None = "pong",
    *,
    usage: tuple[int, int] | None = (7, 3),
    model: str = "served-model",
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=(
            SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
        ),
    )


class FakeClient:
    """Stands in for ``openai.OpenAI``; each ``create`` call consumes the next outcome."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _provider(client: FakeClient, sleeps: list[float] | None = None, **kwargs: Any):
    return OpenAICompatibleProvider(
        "test-api",
        api_key="k",
        base_url="https://api.example.test/v1",
        model="m",
        client=client,
        sleep=(sleeps if sleeps is not None else []).append,
        **kwargs,
    )


def test_happy_path_maps_response_and_sends_exact_kwargs() -> None:
    client = FakeClient(_completion("hello"))
    response = _provider(client).complete(HI, temperature=0.5)

    assert client.calls == [
        {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.5}
    ]
    assert response.text == "hello"
    assert response.model == "served-model"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (7, 3)
    assert response.cached is False
    assert response.latency_s is not None
    assert response.latency_s >= 0


def test_max_tokens_is_sent_only_when_given() -> None:
    client = FakeClient(_completion(), _completion())
    provider = _provider(client)
    provider.complete(HI)
    provider.complete(HI, max_tokens=5)
    assert "max_tokens" not in client.calls[0]
    assert client.calls[1]["max_tokens"] == 5


def test_missing_usage_and_content_degrade_gracefully() -> None:
    response = _provider(FakeClient(_completion(None, usage=None))).complete(HI)
    assert response.text == ""
    assert response.usage.input_tokens is None
    assert response.usage.output_tokens is None


def test_json_schema_becomes_response_format() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    client = FakeClient(_completion('{"a": 1}'))
    _provider(client).complete(HI, json_schema=schema)
    assert client.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema, "strict": False},
    }


def test_bad_request_falls_back_to_no_response_format() -> None:
    client = FakeClient(_make_status_error(openai.BadRequestError, 400), _completion("ok"))
    sleeps: list[float] = []
    response = _provider(client, sleeps).complete(HI, json_schema={"type": "object"})

    assert response.text == "ok"
    assert len(client.calls) == 2
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]
    assert sleeps == []


def test_bad_request_without_response_format_is_an_error() -> None:
    client = FakeClient(_make_status_error(openai.BadRequestError, 400))
    with pytest.raises(LLMError) as caught:
        _provider(client).complete(HI)
    assert caught.value.status == 400
    assert len(client.calls) == 1


def test_bad_request_fallback_happens_only_once() -> None:
    client = FakeClient(
        _make_status_error(openai.BadRequestError, 400),
        _make_status_error(openai.BadRequestError, 400),
    )
    with pytest.raises(LLMError) as caught:
        _provider(client).complete(HI, json_schema={"type": "object"})
    assert caught.value.status == 400
    assert len(client.calls) == 2


def test_rate_limit_is_retried_with_backoff() -> None:
    client = FakeClient(_make_status_error(openai.RateLimitError, 429), _completion("ok"))
    sleeps: list[float] = []
    response = _provider(client, sleeps).complete(HI)
    assert response.text == "ok"
    assert len(client.calls) == 2
    assert sleeps == [1.0]


def test_connection_error_is_retried() -> None:
    client = FakeClient(_connection_error(), _connection_error(), _completion("ok"))
    sleeps: list[float] = []
    assert _provider(client, sleeps).complete(HI).text == "ok"
    assert sleeps == [1.0, 2.0]


def test_auth_error_is_not_retried() -> None:
    client = FakeClient(_make_status_error(openai.AuthenticationError, 401))
    sleeps: list[float] = []
    with pytest.raises(LLMError) as caught:
        _provider(client, sleeps).complete(HI)
    assert caught.value.status == 401
    assert "status 401" in (caught.value.body or "")
    assert len(client.calls) == 1
    assert sleeps == []


def test_server_errors_exhaust_retries_then_raise() -> None:
    client = FakeClient(*(_make_status_error(openai.APIStatusError, 503) for _ in range(3)))
    sleeps: list[float] = []
    with pytest.raises(LLMError) as caught:
        _provider(client, sleeps).complete(HI)
    assert caught.value.status == 503
    assert len(client.calls) == 3  # first try + max_retries=2
    assert sleeps == [1.0, 2.0]


def test_max_retries_zero_disables_retrying() -> None:
    client = FakeClient(_make_status_error(openai.RateLimitError, 429))
    with pytest.raises(LLMError) as caught:
        _provider(client, max_retries=0).complete(HI)
    assert caught.value.status == 429
    assert len(client.calls) == 1


def test_connection_failure_has_no_status() -> None:
    client = FakeClient(_connection_error())
    with pytest.raises(LLMError) as caught:
        _provider(client, max_retries=0).complete(HI)
    assert caught.value.status is None


def test_each_call_is_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="classdeck.llm"):
        _provider(FakeClient(_completion())).complete(HI)
    records = [r for r in caplog.records if r.name == "classdeck.llm"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "test-api" in message
    assert "in=7" in message
    assert "out=3" in message


def test_default_client_is_built_without_network() -> None:
    provider = OpenAICompatibleProvider(
        "api:api.example.test", api_key="k", base_url="https://api.example.test/v1", model="m"
    )
    assert (provider.name, provider.model) == ("api:api.example.test", "m")
