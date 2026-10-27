from __future__ import annotations

import pytest

from classdeck.llm import (
    ImagePart,
    LLMError,
    LLMResponse,
    Message,
    MockProvider,
    TextPart,
    Usage,
    UsageMeter,
    estimate_cost_usd,
    pricing,
)

USER_HI = [Message(role="user", content="hi")]


def test_responses_are_returned_in_order() -> None:
    provider = MockProvider(["one", "two"])
    assert provider.complete(USER_HI).text == "one"
    assert provider.complete(USER_HI).text == "two"


def test_exhausted_queue_raises() -> None:
    provider = MockProvider(["only"])
    provider.complete(USER_HI)
    with pytest.raises(LLMError, match="canned responses"):
        provider.complete(USER_HI)


def test_empty_queue_raises_immediately() -> None:
    with pytest.raises(LLMError):
        MockProvider([]).complete(USER_HI)


def test_handler_receives_messages() -> None:
    provider = MockProvider(handler=lambda messages: f"echo:{messages[-1].content}")
    assert provider.complete(USER_HI).text == "echo:hi"
    assert provider.complete(USER_HI).text == "echo:hi"


def test_no_arguments_gives_a_default_reply() -> None:
    assert MockProvider().complete(USER_HI).text


def test_responses_and_handler_are_exclusive() -> None:
    with pytest.raises(ValueError, match="either"):
        MockProvider(["a"], handler=lambda _: "b")


def test_calls_are_recorded_as_openai_dicts() -> None:
    provider = MockProvider(["ok"])
    schema = {"type": "object"}
    provider.complete(USER_HI, temperature=0.7, max_tokens=9, json_schema=schema)
    assert provider.calls == [
        {
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
            "max_tokens": 9,
            "json_schema": schema,
        }
    ]


def test_identity_and_usage_estimate() -> None:
    provider = MockProvider(["x" * 40], model="mock-2")
    response = provider.complete([Message(role="user", content="y" * 80)])
    assert (provider.name, provider.model, response.model) == ("mock", "mock-2", "mock-2")
    assert response.usage == Usage(input_tokens=20, output_tokens=10)
    assert response.cached is False


def test_message_serialisation() -> None:
    assert USER_HI[0].to_openai() == {"role": "user", "content": "hi"}
    message = Message(
        role="user",
        content=[
            TextPart(text="what is this?"),
            ImagePart(url="https://example.test/a.png", detail="low"),
            ImagePart(url="data:image/png;base64,AAAA"),
        ],
    )
    assert message.to_openai() == {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {
                "type": "image_url",
                "image_url": {"url": "https://example.test/a.png", "detail": "low"},
            },
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }


def test_message_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        Message.model_validate({"role": "user", "content": "hi", "extra": 1})


def test_usage_meter_sums_billed_tokens_only() -> None:
    meter = UsageMeter()
    fresh = LLMResponse(text="a", model="m", usage=Usage(input_tokens=10, output_tokens=4))
    unknown = LLMResponse(text="b", model="m", usage=Usage())
    cached = fresh.model_copy(update={"cached": True})
    for response in (fresh, unknown, cached):
        meter.add(response)
    assert (meter.calls, meter.cached_calls) == (3, 1)
    assert (meter.input_tokens, meter.output_tokens) == (10, 4)
    assert meter.summary() == {
        "calls": 3,
        "cached_calls": 1,
        "input_tokens": 10,
        "output_tokens": 4,
    }


def test_cost_is_none_for_unknown_model_or_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert estimate_cost_usd("not-a-priced-model", usage) is None
    monkeypatch.setitem(pricing.PRICES, "priced", (2.0, 8.0))
    assert estimate_cost_usd("priced", usage) == pytest.approx(6.0)
    assert estimate_cost_usd("priced", Usage(input_tokens=5)) is None
