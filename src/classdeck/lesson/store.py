"""JSON file storage for lessons and source documents, plus the cross-document invariant check.

Files are written atomically (temp file in the same directory, then ``os.replace``) so a crash
never leaves a half-written lesson. Invariant 2 of ``docs/lesson-model.md`` — every citation
resolves to a real span — needs the source documents, so it lives here and not in the schema.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from classdeck.lesson.schema import Citation, Lesson, SourceDocument, VocabularyBlock

_LESSON_SUFFIX = ".lesson.json"
_SOURCE_SUFFIX = ".json"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class LessonNotFound(FileNotFoundError):
    """No stored lesson has the requested id."""


class SourceNotFound(FileNotFoundError):
    """No stored source document has the requested id."""


def _check_id(value: str) -> str:
    """Ids become file names, so anything that could escape the directory is refused."""
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid id {value!r}: use letters, digits, '_' and '-' only")
    return value


def _write_atomic(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = model.model_dump_json(indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _list_ids(directory: Path, suffix: str) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.name.removesuffix(suffix) for p in directory.glob(f"*{suffix}") if p.is_file())


class LessonStore:
    """Lessons as ``<root>/lessons/<lesson_id>.lesson.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, lesson_id: str) -> Path:
        return self.root / "lessons" / f"{_check_id(lesson_id)}{_LESSON_SUFFIX}"

    def save(self, lesson: Lesson) -> Path:
        """Stamp ``updated_at`` and write the lesson atomically."""
        path = self.path_for(lesson.id)
        lesson.touch()
        _write_atomic(path, lesson)
        return path

    def load(self, lesson_id: str) -> Lesson:
        path = self.path_for(lesson_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise LessonNotFound(f"no lesson {lesson_id!r} under {path.parent}") from error
        return Lesson.model_validate_json(text)

    def list_ids(self) -> list[str]:
        return _list_ids(self.root / "lessons", _LESSON_SUFFIX)


class SourceStore:
    """Source documents as ``<root>/sources/<doc_id>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, doc_id: str) -> Path:
        return self.root / "sources" / f"{_check_id(doc_id)}{_SOURCE_SUFFIX}"

    def save(self, document: SourceDocument) -> Path:
        path = self.path_for(document.doc_id)
        _write_atomic(path, document)
        return path

    def load(self, doc_id: str) -> SourceDocument:
        path = self.path_for(doc_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SourceNotFound(f"no source document {doc_id!r} under {path.parent}") from error
        return SourceDocument.model_validate_json(text)

    def list_ids(self) -> list[str]:
        return _list_ids(self.root / "sources", _SOURCE_SUFFIX)


def find_dangling_citations(
    lesson: Lesson, sources: Mapping[str, SourceDocument]
) -> list[tuple[str, Citation, str]]:
    """Invariant 2: citations that do not resolve to a span in a listed source document.

    Returns ``(block_id, citation, reason)`` for each offender, in block order. Block sources
    and vocabulary item images are both checked.
    """
    listed = {ref.doc_id for ref in lesson.sources}
    problems: list[tuple[str, Citation, str]] = []
    for block_id, block in lesson.blocks.items():
        citations = list(block.sources)
        if isinstance(block, VocabularyBlock):
            citations.extend(item.image for item in block.content.items if item.image is not None)
        for citation in citations:
            reason = _dangling_reason(citation, listed, sources)
            if reason is not None:
                problems.append((block_id, citation, reason))
    return problems


def _dangling_reason(
    citation: Citation, listed: set[str], sources: Mapping[str, SourceDocument]
) -> str | None:
    if citation.doc_id not in listed:
        return f"document {citation.doc_id!r} is not listed in the lesson's sources"
    document = sources.get(citation.doc_id)
    if document is None:
        return f"document {citation.doc_id!r} is not available"
    if document.find_span(citation.page, citation.span_id) is None:
        return f"span {citation.span_id!r} not found on page {citation.page} of {citation.doc_id!r}"
    return None
