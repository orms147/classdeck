"""A teacher's own plan (a Word file) as *reference* for one secondary lesson.

``docs/secondary-units.md``, "Drafting a secondary lesson", step 6. A unit plan holds one header
table per period ("Lesson 1: Getting started" ...) followed by the objectives and the procedure
table of that period. :func:`reference_for_lesson` finds the header of the wanted lesson, takes the
content up to the next period's header, drops what must not reach the pupils' slides (digital
competence, special-needs sections, dates) and caps the rest. The result may inform timing and
warm-up ideas; it never overrides the textbook.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

REFERENCE_CHARS = 4000
"""Longest reference text handed to the model."""

LESSON_NAME_PATTERNS: dict[int, str] = {
    1: r"getting\s+started",
    2: r"a\s+closer\s+look\s*1",
    3: r"a\s+closer\s+look\s*2",
    4: r"communication",
    5: r"skills\s*1",
    6: r"skills\s*2",
    7: r"looking\s+back",
}

_DROP_LINE = re.compile(
    r"digital|google|hoclieu|hoc\s+lieu|date\s+of\s+(?:planning|teaching)|^\s*week\s*:",
    re.IGNORECASE,
)
_SPECIAL_NEEDS = re.compile(r"disabilit|hskt|special\s+(?:educational\s+)?needs", re.IGNORECASE)
_OBJECTIVES = re.compile(r"^\s*(?:I\.\s*)?objectives(?!\w)", re.IGNORECASE)
_PROCEDURE = re.compile(r"^\s*(?:III\.\s*)?procedure(?!\w)", re.IGNORECASE)
_NEW_WORDS = re.compile(r"^\W*new\s+words", re.IGNORECASE)
_HEADER_START = re.compile(r"^\s*date\s+of\s+planning", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\s*[=\-_*]{5,}\s*$")
_SPACES = re.compile(r"\s+")


def _blocks(document) -> Iterator[tuple[str, object]]:
    """The body of the document in order: ``("p", Paragraph)`` and ``("t", Table)``."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(element, document)
        elif tag == "tbl":
            yield "t", Table(element, document)


def _cells(table) -> list[str]:
    """The text of every cell of a table, merged cells (repeated by python-docx) once."""
    texts: list[str] = []
    for row in table.rows:
        previous = None
        for cell in row.cells:
            if cell._tc is previous:  # a merged cell shows up once per column it spans
                continue
            previous = cell._tc
            if cell.text.strip():
                texts.append(cell.text)
    return texts


def _is_header(table) -> bool:
    return bool(_HEADER_START.match(" ".join(_cells(table))))


def _is_lesson_header(table, lesson_no: int) -> bool:
    text = " ".join(_cells(table))
    named = LESSON_NAME_PATTERNS.get(lesson_no)
    return bool(
        named and re.search(rf"lesson\s*{lesson_no}\s*[:.\-]?\s*{named}", text, re.IGNORECASE)
    )


def _find_header(items: list[tuple[str, object]], lesson_no: int) -> int | None:
    for index, (kind, item) in enumerate(items):
        if kind == "t" and _is_lesson_header(item, lesson_no):
            return index
    # A plan that numbers the lesson but writes another name for it.
    for index, (kind, item) in enumerate(items):
        if kind == "t" and re.search(
            rf"lesson\s*{lesson_no}\s*[:.\-]", " ".join(_cells(item)), re.I
        ):
            return index
    return None


class _Filter:
    """Line filter with the little state the special-needs and objectives sections need."""

    def __init__(self) -> None:
        self.skipping = False
        self.first_after = False
        self.objectives = False

    def reset_scope(self) -> None:
        """A new table cell starts: bullets of the previous cell do not continue here."""
        self.skipping = False

    def lines(self, text: str) -> list[str]:
        kept: list[str] = []
        for raw in text.splitlines():
            line = _SPACES.sub(" ", raw).strip()
            if not line:
                continue
            if _SPECIAL_NEEDS.search(line):
                self.skipping, self.first_after = True, True
                continue
            if self.skipping:
                bullet = line[0] in "-+*•"
                if bullet or (self.first_after and line.endswith(":")):
                    self.first_after = False
                    continue
                self.skipping = False
            if _PROCEDURE.match(line):
                self.objectives = False
            elif _OBJECTIVES.match(line):
                self.objectives = True
            if _DROP_LINE.search(line) or _SEPARATOR.match(line):
                continue
            if self.objectives and not _NEW_WORDS.match(line):
                continue  # the plan's objectives restate the curriculum; the new words stay
            kept.append(line)
        return kept


def clean_lines(text: str) -> list[str]:
    """The lines of ``text`` that may be shown to the model.

    Dropped: lines about digital competence, Google tools and the school platform, dates and the
    week, special-needs sections (the line that opens one and the bullets under it) and the
    objectives block (its list of new words stays).
    """
    return _Filter().lines(text)


def _item_lines(kind: str, item, state: _Filter) -> list[str]:
    if kind == "p":
        return state.lines(item.text)
    lines: list[str] = []
    for cell in _cells(item):
        state.reset_scope()
        lines.extend(state.lines(cell))
    return lines


def reference_for_lesson(path: Path, lesson_no: int, *, limit: int = REFERENCE_CHARS) -> str:
    """The text of lesson ``lesson_no`` in the plan at ``path``, cleaned and capped at ``limit``.

    ``""`` when the plan holds no header for that lesson. Raises ``OSError`` or
    ``docx.opc.exceptions.PackageNotFoundError`` for an unreadable file.
    """
    import docx

    document = docx.Document(str(path))
    items = list(_blocks(document))
    start = _find_header(items, lesson_no)
    if start is None:
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    state = _Filter()
    for offset, (kind, item) in enumerate(items[start:]):
        if offset and kind == "t" and _is_header(item):
            break
        for line in _item_lines(kind, item, state):
            if line not in seen:  # the plan repeats a cell's text in the merged columns
                seen.add(line)
                lines.append(line)
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut[: cut.rfind("\n")] if "\n" in cut else cut
