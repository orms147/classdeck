"""Typed model output: ask for JSON, parse it, validate it, and repair once on failure."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from classdeck.llm.base import LLMError, LLMProvider, Message, TextPart

_SCHEMA_INSTRUCTION = "Respond with a single JSON object matching this JSON Schema, no prose:"
_FENCE = re.compile(r"```[\w+-]*[ \t]*\n?(.*?)(?:```|$)", re.DOTALL)
_MAX_REPORTED_ERRORS = 10


class StructuredOutputError(LLMError):
    """The model never produced output that parses and validates; ``attempts`` has every reply."""

    def __init__(self, message: str, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts


def extract_json(text: str) -> str:
    """Return the JSON payload inside ``text``, dropping code fences and surrounding prose."""
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return text.strip()
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end < start:
        return text.strip()
    return text[start : end + 1]


def complete_json[T: BaseModel](
    provider: LLMProvider,
    messages: list[Message],
    model_cls: type[T],
    *,
    retries: int = 1,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> T:
    schema = model_cls.model_json_schema()
    conversation = _with_schema_instruction(messages, schema)
    attempts: list[str] = []
    error = ""
    for attempt in range(retries + 1):
        response = provider.complete(
            conversation, temperature=temperature, max_tokens=max_tokens, json_schema=schema
        )
        attempts.append(response.text)
        try:
            return model_cls.model_validate(json.loads(extract_json(response.text)))
        except json.JSONDecodeError as exc:
            error = f"The reply is not valid JSON: {exc}"
        except ValidationError as exc:
            error = "The JSON does not match the schema:\n" + _format_errors(exc)
        if attempt < retries:
            conversation = [
                *conversation,
                Message(role="assistant", content=response.text),
                Message(
                    role="user",
                    content=f"{error}\nRespond again with a single JSON object matching the "
                    "schema, no prose.",
                ),
            ]
    raise StructuredOutputError(
        f"No valid {model_cls.__name__} after {len(attempts)} attempt(s). Last error: {error}",
        attempts,
    )


def _with_schema_instruction(messages: list[Message], schema: dict) -> list[Message]:
    instruction = f"{_SCHEMA_INSTRUCTION}\n{json.dumps(schema)}"
    result = list(messages)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if message.role != "user":
            continue
        if isinstance(message.content, str):
            content: str | list = f"{message.content}\n\n{instruction}"
        else:
            content = [*message.content, TextPart(text=instruction)]
        result[index] = message.model_copy(update={"content": content})
        return result
    result.append(Message(role="user", content=instruction))
    return result


def _format_errors(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False)
    lines = [
        f"- {'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}"
        for err in errors[:_MAX_REPORTED_ERRORS]
    ]
    if len(errors) > _MAX_REPORTED_ERRORS:
        lines.append(f"- ... and {len(errors) - _MAX_REPORTED_ERRORS} more")
    return "\n".join(lines)
