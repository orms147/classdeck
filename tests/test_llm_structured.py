from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from classdeck.llm import (
    LLMError,
    Message,
    MockProvider,
    StructuredOutputError,
    TextPart,
    complete_json,
    extract_json,
)


class Answer(BaseModel):
    word: str
    count: int


GOOD = '{"word": "cat", "count": 2}'
MISSING_FIELD = '{"word": "cat"}'
USER_Q = [Message(role="user", content="Pick a word.")]


def test_success_sends_schema_instruction_and_schema() -> None:
    provider = MockProvider([GOOD])
    result = complete_json(provider, USER_Q, Answer)

    assert result == Answer(word="cat", count=2)
    (call,) = provider.calls
    sent = call["messages"][-1]["content"]
    assert sent.startswith("Pick a word.")
    assert "Respond with a single JSON object matching this JSON Schema, no prose:" in sent
    assert json.dumps(Answer.model_json_schema()) in sent
    assert call["json_schema"] == Answer.model_json_schema()
    assert call["temperature"] == 0.0


def test_input_messages_are_not_mutated() -> None:
    complete_json(MockProvider([GOOD]), USER_Q, Answer)
    assert USER_Q[0].content == "Pick a word."


def test_instruction_goes_on_the_last_user_message() -> None:
    messages = [
        Message(role="system", content="You are terse."),
        Message(role="user", content="first"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="second"),
    ]
    provider = MockProvider([GOOD])
    complete_json(provider, messages, Answer, max_tokens=50)
    sent = provider.calls[0]["messages"]
    assert sent[1]["content"] == "first"
    assert "JSON Schema" in sent[3]["content"]
    assert provider.calls[0]["max_tokens"] == 50


def test_user_message_is_added_when_missing() -> None:
    provider = MockProvider([GOOD])
    complete_json(provider, [Message(role="system", content="rules")], Answer)
    sent = provider.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert "JSON Schema" in sent[1]["content"]


def test_instruction_is_appended_as_a_part_to_multimodal_content() -> None:
    message = Message(role="user", content=[TextPart(text="look")])
    provider = MockProvider([GOOD])
    complete_json(provider, [message], Answer)
    parts = provider.calls[0]["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert "JSON Schema" in parts[1]["text"]


def test_fenced_reply_with_prose_is_accepted() -> None:
    reply = f"Sure! Here you go:\n```json\n{GOOD}\n```\nHope that helps."
    assert complete_json(MockProvider([reply]), USER_Q, Answer).count == 2


def test_repair_after_one_bad_output() -> None:
    provider = MockProvider([MISSING_FIELD, GOOD])
    result = complete_json(provider, USER_Q, Answer)

    assert result.count == 2
    assert len(provider.calls) == 2
    retry = provider.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[1]["content"] == MISSING_FIELD
    assert "count" in retry[2]["content"]
    assert "Field required" in retry[2]["content"]


def test_repair_after_invalid_json() -> None:
    provider = MockProvider(["not json at all", GOOD])
    complete_json(provider, USER_Q, Answer)
    assert "not valid JSON" in provider.calls[1]["messages"][-1]["content"]


def test_failure_raises_with_all_attempts() -> None:
    provider = MockProvider([MISSING_FIELD, "still wrong"])
    with pytest.raises(StructuredOutputError) as caught:
        complete_json(provider, USER_Q, Answer)
    assert caught.value.attempts == [MISSING_FIELD, "still wrong"]
    assert isinstance(caught.value, LLMError)
    assert "Answer" in str(caught.value)


def test_retries_zero_means_a_single_attempt() -> None:
    provider = MockProvider([MISSING_FIELD, GOOD])
    with pytest.raises(StructuredOutputError) as caught:
        complete_json(provider, USER_Q, Answer, retries=0)
    assert len(caught.value.attempts) == 1
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('```JSON {"a": 1}```', '{"a": 1}'),
        ('Here it is: {"a": {"b": [1, 2]}} - done.', '{"a": {"b": [1, 2]}}'),
        ('Result:\n```json\n[{"a": 1}, {"a": 2}]\n```\nThanks', '[{"a": 1}, {"a": 2}]'),
        ('```json\n{"a": 1}', '{"a": 1}'),
        ("  no json here  ", "no json here"),
        ("} stray {", "} stray {"),
    ],
)
def test_extract_json(text: str, expected: str) -> None:
    assert extract_json(text) == expected
