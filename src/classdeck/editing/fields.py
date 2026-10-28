"""Edit one text field of a block by its path: the setter behind pencil-and-Save on the deck.

A path is the JSON-pointer-like string the deck renderer puts in ``data-field``
(``content/items/0/word``, ``content/lines/3/text``, ``teacher_note``; ``docs/output-formats.md``,
"Editing on the slide"). :func:`set_field` and :func:`set_fields` check a path against the
block's content model: unknown paths, paths through the machinery of a block (ids, citations,
checks, enum values, numbers, answer flags) and targets that are not text are rejected. Edits
that would break an exercise's answer key are rejected with a message the teacher can act on:

* the text of a gap-fill sentence must keep its number of blanks (``___``);
* renaming a multiple-choice option that is the right answer renames the answer with it;
* a paragraph keeps only the highlighted words that still occur in its edited text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import Enum
from typing import Any, get_args

from pydantic import BaseModel, ValidationError

from classdeck.lesson.schema import Block, BlockAdapter, GapFillBlock, ReadingBlock

_BLANK = re.compile(r"_{2,}")

NOT_EDITABLE = frozenset(
    {
        "id",
        "kind",
        "sources",
        "provenance",
        "depends_on",
        "checks",
        "slide_hint",
        "outcome_ids",
        "image",
        "generated_picture",
    }
)
"""Path segments that are never edited as text (identity, citations, results, curriculum ids)."""


class FieldEditError(ValueError):
    """An edit that cannot be applied; ``block_id`` and ``field`` say where."""

    def __init__(self, message: str, *, block_id: str | None = None, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.block_id = block_id
        self.field = field


def split_path(path: str) -> list[str]:
    """``content/items/0/word`` -> ``["content", "items", "0", "word"]``; ``~1`` is ``/``."""
    if not isinstance(path, str) or not path.strip("/"):
        raise FieldEditError("the field path is empty")
    return [part.replace("~1", "/").replace("~0", "~") for part in path.strip("/").split("/")]


def _nullable(model: type[BaseModel], name: str) -> bool:
    return type(None) in get_args(model.model_fields[name].annotation)


def _normalise(path: str, value: str) -> str:
    """Pupil-facing text is one line of single spaces; teacher notes keep their lines."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if path.strip("/") == "teacher_note":
        lines = [" ".join(line.split()) for line in value.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        while lines and not lines[0]:
            lines.pop(0)
        return "\n".join(lines)
    return " ".join(value.split())


class _Target:
    """Where a path ends: the containing dict/list in the dumped block, the key and the old text."""

    def __init__(self, container: Any, key: str | int, nullable: bool) -> None:
        self.container = container
        self.key = key
        self.nullable = nullable


def _resolve(block: Block, data: dict[str, Any], path: str) -> _Target:
    segments = split_path(path)
    if segments[0] not in ("content", "teacher_note"):
        raise FieldEditError(f"{path!r} is not editable text")
    if segments[0] == "teacher_note" and len(segments) > 1:
        raise FieldEditError(f"unknown field {path!r}")
    obj: Any = block
    node: Any = data
    nullable = False
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment in NOT_EDITABLE:
            raise FieldEditError(f"{path!r} is not editable text")
        if isinstance(obj, BaseModel):
            if segment not in type(obj).model_fields:
                raise FieldEditError(f"unknown field {path!r}: no {segment!r} here")
            nullable = _nullable(type(obj), segment)
            child_obj, child_node = getattr(obj, segment), node[segment]
        elif isinstance(obj, list):
            if not segment.isdigit() or int(segment) >= len(obj):
                raise FieldEditError(f"unknown field {path!r}: no item {segment!r}")
            nullable = False
            child_obj, child_node = obj[int(segment)], node[int(segment)]
        elif isinstance(obj, dict):
            if segment not in obj:
                raise FieldEditError(f"unknown field {path!r}: no entry {segment!r}")
            nullable = False
            child_obj, child_node = obj[segment], node[segment]
        else:
            raise FieldEditError(f"unknown field {path!r}: {segment!r} is inside a plain value")
        if last:
            if isinstance(child_obj, Enum) or not (child_obj is None or isinstance(child_obj, str)):
                raise FieldEditError(f"{path!r} is not text and cannot be edited here")
            key: str | int = int(segment) if isinstance(obj, list) else segment
            return _Target(node, key, nullable)
        obj, node = child_obj, child_node
    raise FieldEditError(f"unknown field {path!r}")  # pragma: no cover - segments is non-empty


def _value_at(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for segment in split_path(path):
        node = node[int(segment)] if isinstance(node, list) else node[segment]
    return node


def _after_rules(block: Block, data: dict[str, Any], path: str, old: str | None, new: str) -> None:
    """Keep the exercise consistent when one text changes (called after the value is set)."""
    segments = split_path(path)
    gap_text = len(segments) == 4 and segments[:2] == ["content", "items"] and segments[3] == "text"
    if isinstance(block, GapFillBlock) and gap_text:
        was, now = len(_BLANK.findall(old or "")), len(_BLANK.findall(new))
        if was != now:
            raise FieldEditError(
                f"The sentence must keep its {was} blank{'s' if was != 1 else ''} (___); "
                f"the new text has {now}. Change the answers separately to change the blanks.",
                block_id=block.id,
                field=path,
            )
    if segments[:2] == ["content", "items"] and len(segments) == 5 and segments[3] == "options":
        item = _value_at(data, "/".join(segments[:3]))
        if item.get("answer") == old and isinstance(item.get("options"), list):
            item["answer"] = new
    paragraph_text = (
        len(segments) == 4 and segments[:2] == ["content", "paragraphs"] and segments[3] == "text"
    )
    if isinstance(block, ReadingBlock) and paragraph_text:
        paragraph = _value_at(data, "/".join(segments[:3]))
        lowered = new.lower()
        paragraph["highlight"] = [
            word for word in paragraph.get("highlight", []) if word.lower() in lowered
        ]


def _clear(error: ValidationError, block_id: str) -> str:
    first = error.errors()[0]
    message = str(first.get("msg", "invalid value"))
    message = message.removeprefix("Value error, ")
    where = ".".join(str(part) for part in first.get("loc", ()) if part != "content")
    if first.get("type") == "string_too_short":
        return f"{block_id}: {where or 'this text'} cannot be empty."
    return f"{block_id}: {message}"


def set_fields(block: Block, edits: Sequence[tuple[str, str]]) -> Block:
    """A copy of ``block`` with every ``(path, text)`` applied, validated once at the end.

    Applying a batch together lets a related pair (a gap answer and its word-box chip) change
    in one step. Raises :class:`FieldEditError` for an unknown path, a target that is not text
    or an edit that would leave the exercise inconsistent; the block is never changed in place.
    """
    data = block.model_dump(mode="json")
    for path, value in edits:
        if not isinstance(value, str):
            raise FieldEditError("the new value must be text", block_id=block.id, field=path)
        try:
            target = _resolve(block, data, path)
        except FieldEditError as exc:
            raise FieldEditError(exc.message, block_id=block.id, field=path) from None
        # A repeated edit of the same field resolves against the updated data, not the model.
        current = target.container[target.key]
        text = _normalise(path, value)
        target.container[target.key] = None if text == "" and target.nullable else text
        try:
            _after_rules(block, data, path, current if isinstance(current, str) else None, text)
        except FieldEditError as exc:
            raise FieldEditError(exc.message, block_id=block.id, field=path) from None
    try:
        return BlockAdapter.validate_python(data)
    except ValidationError as exc:
        field = edits[0][0] if len(edits) == 1 else None
        raise FieldEditError(_clear(exc, block.id), block_id=block.id, field=field) from exc


def set_field(block: Block, path: str, value: str) -> Block:
    """:func:`set_fields` for one edit."""
    return set_fields(block, [(path, value)])


def editable_fields(block: Block) -> list[str]:
    """Every path :func:`set_field` accepts on this block, in document order (for tests, tools)."""
    found: list[str] = []

    def walk(obj: Any, prefix: list[str]) -> None:
        if isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                if name in NOT_EDITABLE and name != "answer":
                    continue
                walk(getattr(obj, name), [*prefix, name])
        elif isinstance(obj, list):
            for index, item in enumerate(obj):
                walk(item, [*prefix, str(index)])
        elif isinstance(obj, dict):
            for key, item in obj.items():
                walk(item, [*prefix, str(key).replace("~", "~0").replace("/", "~1")])
        elif isinstance(obj, str) and not isinstance(obj, Enum) and prefix:
            found.append("/".join(prefix))

    walk(block.content, ["content"])
    if block.teacher_note is not None:
        found.append("teacher_note")
    return found
