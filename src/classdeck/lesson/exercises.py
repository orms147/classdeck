"""Plain-text views of the exercise blocks, shared by every writer, the checks and the handoff.

An exercise block (``reading``, ``match``, ``gap_fill``, ``choice``, ``true_false``,
``rule_box``, ``question_set``) is rendered differently by the HTML deck, the PowerPoint, the Word
plan and the prompt packs, but they all need the same few strings: the text the pupils see, the
answer key and the labels. Keeping them here means a new exercise kind is handled in one place.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from classdeck.lesson.schema import (
    Block,
    ChoiceBlock,
    GalleryBlock,
    GapFillBlock,
    GapItem,
    GridBlock,
    MatchBlock,
    QuestionSetBlock,
    ReadingBlock,
    RuleBoxBlock,
    SelfCheckBlock,
    SoundSortBlock,
    TrueFalseBlock,
)

BLANK = re.compile(r"_{2,}")

KIND_LABEL: dict[str, str] = {
    "reading": "Reading",
    "match": "Match",
    "gap_fill": "Fill in the gaps",
    "choice": "Choose",
    "true_false": "True or false",
    "rule_box": "Remember!",
    "question_set": "Questions",
    "sound_sort": "Sounds",
    "gallery": "Look",
    "grid": "Table",
    "self_check": "Now I can ...",
}
"""Short slide title for each exercise kind (a rule box and a reading use their own title)."""

TRUE, FALSE = "True", "False"


def letter(index: int) -> str:
    """``A`` for 0, ``B`` for 1 ... (the right-hand labels and the choice options)."""
    return chr(ord("A") + index)


def filled(item: GapItem) -> str:
    """The gap sentence with every blank replaced by its answer, in order."""
    answers = iter(item.answers)
    return BLANK.sub(lambda _match: next(answers, ""), item.text)


def slashed(sound: str) -> str:
    """A sound as the textbook writes it: ``/e/``."""
    return f"/{sound}/"


def picture_items(texts: list[str], images: list[object]) -> list[str]:
    """The texts of a matching column that are printed words: a picture item is not text."""
    return [text for n, text in enumerate(texts) if not (n < len(images) and images[n])]


def exercise_title(block: Block) -> str | None:
    """The heading of an exercise slide or row, or ``None`` for any other kind."""
    if isinstance(block, ReadingBlock):
        return block.content.title or KIND_LABEL["reading"]
    if isinstance(block, RuleBoxBlock | SelfCheckBlock):
        return block.content.title
    if isinstance(block, GalleryBlock) and block.content.title:
        return block.content.title
    label = KIND_LABEL.get(block.kind)
    if label is None:
        return None
    number = getattr(block.content, "exercise_no", None)
    return f"{label} {number}" if number else label


def instruction_of(block: Block) -> str:
    """The textbook's instruction line (``Read the text.``), or ``""``."""
    return getattr(block.content, "instruction", "") or ""


def page_strings(block: Block) -> list[str]:
    """Everything the page shows for this exercise, without answers: what a source span restates."""
    strings: list[str] = []
    match block:
        case ReadingBlock():
            strings = [block.content.title, *(p.text for p in block.content.paragraphs)]
        case MatchBlock():
            content = block.content
            strings = [
                *picture_items(content.left, content.left_images),
                *picture_items(content.right, content.right_images),
            ]
        case GapFillBlock():
            strings = [*(item.text for item in block.content.items), *block.content.word_box]
        case ChoiceBlock():
            for item in block.content.items:
                strings += [item.stem, *item.options]
        case TrueFalseBlock():
            strings = [item.statement for item in block.content.items]
        case RuleBoxBlock():
            strings = [block.content.title, *block.content.points, *block.content.examples]
        case QuestionSetBlock():
            strings = [block.content.prompt, *(q.question for q in block.content.questions)]
        case SoundSortBlock():
            strings = [
                *(slashed(sound) for sound in block.content.sounds),
                *(item.word for item in block.content.words),
                *(sentence.text for sentence in block.content.sentences),
            ]
        case GalleryBlock():
            strings = [block.content.title, *block.content.questions]
        case GridBlock():
            strings = [
                *block.content.header,
                *(cell for row in block.content.rows for cell in row),
            ]
        case SelfCheckBlock():
            strings = [block.content.title, *block.content.items]
        case _:
            return []
    return [text for text in (instruction_of(block), *strings) if text.strip()]


def revealed_answers(block: Block) -> list[str]:
    """Answers the pupils see when they tap to reveal (gap words)."""
    if isinstance(block, GapFillBlock):
        return [answer for item in block.content.items for answer in item.answers]
    return []


def teacher_answers(block: Block) -> list[str]:
    """Answers meant for the teacher only (sample answers to discussion questions)."""
    if isinstance(block, QuestionSetBlock):
        return [q.answer for q in block.content.questions if q.answer.strip()]
    return []


def pupil_strings(block: Block) -> list[str]:
    """The English a pupil sees or hears in this exercise, including revealed gap words."""
    return [*page_strings(block), *revealed_answers(block)]


def english_strings(block: Block) -> list[str]:
    """:func:`pupil_strings` plus the teacher's sample answers; glosses are left out."""
    return [*pupil_strings(block), *teacher_answers(block)]


class ItemGroups(NamedTuple):
    """An exercise cut into the parts a slide can be split on."""

    header: list[str]
    """Lines every part repeats (a word box, a rule box title, a reading title)."""
    groups: list[list[str]]
    """One entry per item (a paragraph, a numbered line, a question with its options)."""
    aside: list[str]
    """Lines that stay whole beside the items (the right-hand column of a matching exercise)."""


def _item_label(text: str, images: list[object], index: int) -> str:
    """A matching item as a line: its text, or ``[picture: description]`` for a picture."""
    return f"[picture: {text}]" if index < len(images) and images[index] else text


def item_groups(block: Block) -> ItemGroups:
    """The exercise as numbered plain lines, grouped by item (no answers)."""
    header: list[str] = []
    groups: list[list[str]] = []
    aside: list[str] = []
    match block:
        case ReadingBlock():
            header = [block.content.title] if block.content.title else []
            groups = [[paragraph.text] for paragraph in block.content.paragraphs]
        case MatchBlock():
            groups = [
                [f"{n}. {_item_label(text, block.content.left_images, n - 1)}"]
                for n, text in enumerate(block.content.left, start=1)
            ]
            aside = [
                f"{letter(i)}. {_item_label(text, block.content.right_images, i)}"
                for i, text in enumerate(block.content.right)
            ]
        case GapFillBlock():
            if block.content.word_box:
                header = ["Word box: " + " / ".join(block.content.word_box)]
            groups = [[f"{n}. {i.text}"] for n, i in enumerate(block.content.items, start=1)]
        case ChoiceBlock():
            for number, item in enumerate(block.content.items, start=1):
                options = "   ".join(f"{letter(i)}. {o}" for i, o in enumerate(item.options))
                groups.append([f"{number}. {item.stem}", "   " + options])
        case TrueFalseBlock():
            groups = [
                [f"{n}. {item.statement}  ({TRUE} / {FALSE})"]
                for n, item in enumerate(block.content.items, start=1)
            ]
        case RuleBoxBlock():
            header = [block.content.title]
            groups = [[f"- {point}"] for point in block.content.points]
            groups += [[f"e.g. {example}"] for example in block.content.examples]
        case QuestionSetBlock():
            header = [block.content.prompt] if block.content.prompt else []
            groups = [[f"{n}. {q.question}"] for n, q in enumerate(block.content.questions, 1)]
        case SoundSortBlock():
            header = ["Sounds: " + "  ".join(slashed(s) for s in block.content.sounds)]
            groups = [[item.word] for item in block.content.words]
            groups += [
                [f"{n}. {sentence.text}"] for n, sentence in enumerate(block.content.sentences, 1)
            ]
        case GalleryBlock():
            header = [block.content.title] if block.content.title else []
            groups = [[f"{n}. {q}"] for n, q in enumerate(block.content.questions, 1)]
            aside = [
                "[picture"
                + (f" {image.label}" if image.label else "")
                + (f": {image.caption}" if image.caption else "")
                + "]"
                for image in block.content.images
            ]
        case GridBlock():
            header = [" | ".join(block.content.header)]
            groups = [[" | ".join(row)] for row in block.content.rows]
        case SelfCheckBlock():
            header = [block.content.title]
            groups = [[f"- {item}"] for item in block.content.items]
    return ItemGroups(header, groups, aside)


def exercise_lines(block: Block) -> list[str]:
    """The exercise as numbered plain lines, as it would be printed (no answers)."""
    parts = item_groups(block)
    return [*parts.header, *(line for group in parts.groups for line in group), *parts.aside]


def answer_pairs(block: Block) -> list[tuple[int, str]]:
    """The answer key as ``(item index, line)`` pairs, so a split slide can show its own part."""
    key: list[tuple[int, str]] = []
    match block:
        case MatchBlock():
            content = block.content
            for left_index in sorted(content.answer):
                right_index = content.answer[left_index]
                left = _item_label(content.left[left_index], content.left_images, left_index)
                right = _item_label(content.right[right_index], content.right_images, right_index)
                key.append(
                    (left_index, f"{left_index + 1}-{letter(right_index)}  {left} = {right}")
                )
        case GapFillBlock():
            key = [(n, f"{n + 1}. {filled(item)}") for n, item in enumerate(block.content.items)]
        case ChoiceBlock():
            for n, item in enumerate(block.content.items):
                option = letter(item.options.index(item.answer))
                key.append((n, f"{n + 1}. {option}. {item.answer}"))
        case TrueFalseBlock():
            key = [
                (n, f"{n + 1}. {TRUE if item.answer else FALSE}")
                for n, item in enumerate(block.content.items)
            ]
        case QuestionSetBlock():
            key = [
                (n, f"{n + 1}. {q.answer}")
                for n, q in enumerate(block.content.questions)
                if q.answer.strip()
            ]
        case GridBlock():
            key = [(n, f"({n + 1}) {answer}") for n, answer in enumerate(block.content.answers)]
        case SoundSortBlock():
            content = block.content
            for sound in content.sounds:
                words = [w.word for w in content.words if w.sound == sound]
                if words:
                    key.append((len(key), f"{slashed(sound)}  {', '.join(words)}"))
            for n, sentence in enumerate(content.sentences):
                marks = ", ".join(f"{t.word} {slashed(t.sound)}" for t in sentence.targets)
                key.append((len(key), f"{n + 1}. {marks}"))
    return key


def answer_key(block: Block) -> list[str]:
    """The answer key as plain lines (``1-C  cat = a small pet``); empty when there is none."""
    return [line for _index, line in answer_pairs(block)]
