"""Synthetic lessons whose decks stress the stage: every exercise kind, long texts, many items.

``make_fit_lesson(secondary=False)`` is a primary lesson (pictures, flashcards, a long
conversation, a chant, an activity with many steps, a summary of many words); ``secondary=True``
is a lower-secondary one (grade 9: long readings, wide exercises). ``make_fit_sources`` and
``write_fit_images`` give the pictures that the lessons cite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from classdeck.lesson import Lesson, SourceDocument
from tests.test_lesson_schema import IPA_HELLO

DOC_ID = "d_fitdeck1"
SHA = "f" * 64
PICTURES = 14


def _image_path(n: int) -> str:
    return f"sources/{DOC_ID}/p1_img{n}.png"


def make_fit_sources() -> SourceDocument:
    spans = [
        {
            "span_id": f"p1.s{n}",
            "kind": "image",
            "text": f"Picture {n}",
            "order": n,
            "bbox": [60.0, 100.0 + n, 520.0, 300.0 + n],
            "image_path": _image_path(n),
        }
        for n in range(1, PICTURES + 1)
    ]
    spans.append({"span_id": "p1.s99", "kind": "paragraph", "text": "Text.", "order": 99})
    return SourceDocument.model_validate(
        {
            "doc_id": DOC_ID,
            "filename": "fit.pdf",
            "sha256": SHA,
            "media_type": "application/pdf",
            "pages": [{"number": 1, "spans": spans}],
        }
    )


def write_fit_images(root: Path) -> None:
    """Flat-colour pictures of different shapes (wide, square, tall) under ``root``."""
    shapes = [(600, 400), (400, 400), (300, 450)]
    for n in range(1, PICTURES + 1):
        width, height = shapes[n % len(shapes)]
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
        pixmap.clear_with(90 + 10 * n)
        path = root / _image_path(n)
        path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(path))


def _cite(n: int) -> dict[str, Any]:
    return {"doc_id": DOC_ID, "page": 1, "span_id": f"p1.s{n}"}


LONG = (
    "The students in our class usually spend a lot of time talking about their favourite "
    "hobbies, healthy food and the best ways to stay fit during the school year"
)
WORDS = [
    ("well-balanced", "equal in amount", "A well-balanced meal has fruit and vegetables."),
    ("stressed out", "very worried and tired", LONG + "."),
    ("priority", "something that matters most", "My first priority is my homework."),
    ("accomplish", "to succeed in doing", "We accomplished the task together."),
    ("delay", "to do later", "Please do not delay your homework."),
    ("due date", "the last day to finish", "The due date is on Friday."),
    ("optimistic", "hopeful", "She is optimistic about her future."),
    ("mood", "the way you feel", "He was in a good mood."),
    ("attitude", "how you think", "Keep a positive attitude."),
    ("strength", "a good quality", "Kindness is her greatest strength."),
    ("habit", "something you do often", "Reading before bed is a good habit."),
    ("routine", "your usual order of doing things", "My morning routine is simple."),
]


def _vocabulary(count: int) -> dict[str, Any]:
    items = []
    for n, (word, meaning, example) in enumerate(WORDS[:count]):
        item: dict[str, Any] = {
            "word": word,
            "ipa": IPA_HELLO,
            "pos": "noun",
            "meaning_vi": meaning,
            "example": example,
        }
        if n % 2 == 0:
            item["image"] = _cite(n + 1)
        items.append(item)
    return {
        "id": "b_vocab",
        "kind": "vocabulary",
        "content": {"items": items},
        "provenance": "generated",
    }


def _dialogue() -> dict[str, Any]:
    lines = []
    for n in range(9):
        speaker = "Mai" if n % 2 == 0 else "Nam"
        text = ("Well, I think so. " * (1 + n % 3)) + f"Line {n + 1} of the conversation."
        lines.append({"speaker": speaker, "text": text})
    return {
        "id": "b_dialogue",
        "kind": "dialogue",
        "content": {
            "context": "Mai and Nam talk about a healthy lifestyle.",
            "lines": lines,
            "highlights": ["I think so", "of the conversation"],
        },
        "provenance": "generated",
    }


def _ex(kind: str, ident: str, number: str, instruction: str, **content: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "kind": kind,
        "content": {"exercise_no": number, "instruction": instruction, **content},
        "sources": [],
        "provenance": "generated",
    }


def _blocks(secondary: bool) -> list[dict[str, Any]]:
    long_options = [
        "to take part in many different sports",
        "to stay at home and watch television",
        "to eat only sweet food every day",
        "to sleep until the late afternoon",
    ]
    blocks: list[dict[str, Any]] = [
        {
            "id": "b_obj",
            "kind": "objective",
            "content": {"text": "Pupils can talk about healthy habits.", "skills": ["speaking"]},
            "provenance": "derived",
        },
        {
            "id": "b_warm",
            "kind": "activity",
            "content": {
                "type": "game",
                "title": "Healthy habits relay",
                "skills": ["speaking", "listening"],
                "interaction": "groups",
                "minutes": 6,
                "instructions": "Work in groups of four. " + LONG + ".",
                "teacher_steps": ["Put pupils in groups.", "Start the timer."],
                "student_steps": [
                    "Stand in a line behind your team's flashcard.",
                    "The first pupil runs to the board and writes a healthy habit.",
                    "Pass the marker to the next pupil in your team without talking.",
                    "Read all the habits your team wrote aloud to the whole class.",
                    "Count one point for each correct and clearly written habit.",
                    "Swap the roles and play the relay again with a new topic.",
                    "The team with the most points wins and chooses the next game.",
                ],
            },
            "provenance": "generated",
        },
        _vocabulary(12),
        {
            "id": "b_pattern",
            "kind": "sentence_pattern",
            "content": {
                "pattern": "I usually ___ before I ___ every morning.",
                "function": "Talk about daily routines and healthy habits",
                "examples": [
                    "I usually stretch before I run every morning.",
                    "I usually brush my teeth before I eat breakfast.",
                    "I usually read before I go to bed.",
                    "I usually pack my bag before I leave home.",
                    "I usually drink water before I do sport.",
                ],
            },
            "provenance": "generated",
        },
        _dialogue(),
        {
            "id": "b_phonics",
            "kind": "phonics",
            "content": {
                "sound": "/θ/",
                "letters": "th",
                "words": ["thin", "think", "thank", "three", "thirty", "thirsty", "thumb", "math"],
                "chant_lines": ["Th, th, think of three!", "Th, th, thank you, thin thumb!"],
            },
            "provenance": "generated",
        },
        *[
            {
                "id": f"b_pic{n}",
                "kind": "image_ref",
                "content": {"caption": f"Picture {n}: a healthy habit", "alt": f"Habit {n}"},
                "sources": [_cite(n)],
                "provenance": "extracted",
            }
            for n in range(1, 8)
        ],
        {
            "id": "b_read",
            "kind": "reading",
            "content": {
                "exercise_no": "1",
                "instruction": "Read the text about healthy living.",
                "title": "Healthy living for teens",
                "paragraphs": [
                    {
                        "text": (LONG + ". ") * (2 + n % 2),
                        "highlight": ["healthy"],
                        **({"image": _cite(3 + n)} if n in (0, 2) else {}),
                    }
                    for n in range(6)
                ],
                "glossary": {"healthy": "in good health", "fit": "strong and well"},
                "image": _cite(2),
            },
            "provenance": "generated",
        },
        _ex(
            "match",
            "b_match",
            "2",
            "Match the words with their meanings.",
            left=[w[0] for w in WORDS[:8]],
            right=[w[1] + " and " + w[2][:40] for w in WORDS[:8]],
            answer={str(n): n for n in range(8)},
        ),
        _ex(
            "match",
            "b_picmatch",
            "3",
            "Match the pictures with the words.",
            left=[f"Picture {n}" for n in range(1, 7)],
            right=["run", "swim", "read", "sleep", "eat", "talk", "walk"],
            left_images=[_cite(n) for n in range(1, 7)],
            answer={str(n): n for n in range(6)},
        ),
        _ex(
            "gap_fill",
            "b_gap",
            "4",
            "Complete the sentences with the words in the box.",
            word_box=[w[0] for w in WORDS[:10]],
            items=[
                {"text": f"{n + 1}. " + LONG[:60] + " ___ every day.", "answer": WORDS[n][0]}
                for n in range(8)
            ],
        ),
        _ex(
            "choice",
            "b_choice",
            "5",
            "Choose the correct answer A, B, C or D.",
            items=[
                {
                    "stem": f"Question {n + 1}: " + LONG[: 40 + 12 * n] + " ___ ?",
                    "options": long_options if n % 2 else ["mind", "feeling", "attitude", "mood"],
                    "answer": long_options[n % 4] if n % 2 else "mood",
                }
                for n in range(7)
            ],
        ),
        _ex(
            "true_false",
            "b_tf",
            "6",
            "Are the sentences true or false?",
            items=[
                {
                    "statement": f"Statement {n + 1}: " + LONG[: 30 + 10 * n] + ".",
                    "answer": n % 2 == 0,
                }
                for n in range(9)
            ],
        ),
        _ex(
            "question_set",
            "b_qset",
            "7",
            "Work in pairs. Ask and answer.",
            prompt="Then tell the class about your partner. " + LONG + ".",
            questions=[
                {
                    "question": f"Question {n + 1}: " + LONG[: 20 + 14 * n] + "?",
                    "answer": "I think it is important to eat well." if n % 2 else "",
                }
                for n in range(8)
            ],
        ),
        _ex(
            "sound_sort",
            "b_sort",
            "8",
            "Sort the words by their sounds.",
            sounds=["θ", "ð", "s"],
            words=[
                {"word": w, "sound": s}
                for w, s in [
                    ("thin", "θ"),
                    ("think", "θ"),
                    ("three", "θ"),
                    ("thank", "θ"),
                    ("this", "ð"),
                    ("that", "ð"),
                    ("those", "ð"),
                    ("mother", "ð"),
                    ("sun", "s"),
                    ("sister", "s"),
                    ("lesson", "s"),
                    ("classroom", "s"),
                    ("breathe", "ð"),
                    ("healthy", "θ"),
                ]
            ],
            sentences=[
                {
                    "text": f"Sentence {n + 1}: this thin sister thinks the sun is hot.",
                    "targets": [
                        {"word": "this", "sound": "ð"},
                        {"word": "thin", "sound": "θ"},
                        {"word": "sun", "sound": "s"},
                    ],
                }
                for n in range(5)
            ],
        ),
        {
            "id": "b_rule",
            "kind": "rule_box",
            "content": {
                "title": "Remember! The present perfect for experiences",
                "points": [
                    "We use the present perfect to talk about experiences in our lives "
                    + LONG[:60]
                    + ".",
                    "Form: have / has + the past participle of the main verb.",
                    "We often use ever, never, already and yet with this tense.",
                    "We do not use a time like yesterday or last week with it.",
                ],
                "examples": [
                    "I have never eaten sushi in my whole life.",
                    "She has already finished her homework for tomorrow.",
                    "Have you ever visited a farm in the countryside?",
                    "They have not seen that film yet.",
                    "He has just come back from a long trip.",
                    "We have lived in this city for ten years.",
                ],
            },
            "provenance": "generated",
        },
        {
            "id": "b_gal",
            "kind": "gallery",
            "content": {
                "exercise_no": "9",
                "instruction": "Look at the pictures and answer the questions.",
                "title": "Healthy habits",
                "images": [
                    {
                        "image": _cite(n),
                        "label": "abcdef"[n - 1],
                        "caption": f"Habit {n}: " + LONG[:30],
                        "alt": f"Habit {n}",
                    }
                    for n in range(1, 7)
                ],
                "questions": [
                    "What can you see in each picture? " + LONG[:50] + "?",
                    "Which habits do you have?",
                    "Which habit is the most difficult for you to keep?",
                ],
            },
            "sources": [],
            "provenance": "generated",
        },
        {
            "id": "b_grid",
            "kind": "grid",
            "content": {
                "exercise_no": "10",
                "instruction": "Complete the survey table.",
                "header": ["Questions", "Student 1", "Student 2", "Student 3"],
                "rows": [
                    ["Question " + str(n) + ": " + LONG[:70] + "?", "", "", ""] for n in range(1, 6)
                ]
                + [["Name", "___", "___", ""]],
                "blank_rows": 3,
                "answers": ["Mai", "Nam"],
            },
            "sources": [],
            "provenance": "generated",
        },
        {
            "id": "b_self",
            "kind": "self_check",
            "content": {
                "title": "Now I can ...",
                "items": [
                    f"talk about habit number {n} in the unit. " + LONG[:40] for n in range(8)
                ],
            },
            "sources": [],
            "provenance": "generated",
        },
        {
            "id": "b_sum",
            "kind": "summary",
            "content": {"bullets": ["Healthy habits", "Daily routines"]},
            "provenance": "generated",
        },
    ]
    if secondary:
        long_para = ((LONG + ". ") * 3).strip()
        for block in blocks:
            if block["kind"] == "reading":
                block["content"]["paragraphs"] = [
                    {"text": long_para, "highlight": ["healthy"]} for _ in range(4)
                ]
    return blocks


def make_fit_lesson(*, secondary: bool = False) -> Lesson:
    blocks = {block["id"]: block for block in _blocks(secondary)}

    pictures = [f"b_pic{n}" for n in range(1, 8)]

    return Lesson.model_validate(
        {
            "id": "l_fit_secondary" if secondary else "l_fit_primary",
            "title": "Unit 3 Healthy living - Skills 1" if secondary else "Unit 3 Food - Lesson 2",
            "grade": 9 if secondary else 4,
            "curriculum": (
                {"version": "gdpt-2018", "unit_id": "ta9.u3", "lesson_no": 5}
                if secondary
                else {"version": "gdpt-2018", "unit_id": "ta4.u3", "lesson_no": 2, "period": 1}
            ),
            "sources": [{"doc_id": DOC_ID, "filename": "fit.pdf", "sha256": SHA}],
            "sections": [
                {
                    "id": "s_warm",
                    "phase": "warm_up",
                    "title": "Warm-up: relay",
                    "minutes": 5,
                    "block_ids": ["b_obj", "b_warm"],
                },
                {
                    "id": "s_pres",
                    "phase": "presentation",
                    "title": "Presentation: words and conversation",
                    "minutes": 12,
                    "block_ids": [
                        "b_vocab",
                        "b_pattern",
                        "b_dialogue",
                        "b_phonics",
                        *pictures,
                        "b_read",
                    ],
                },
                {
                    "id": "s_prac",
                    "phase": "practice",
                    "title": "Practice: exercises",
                    "minutes": 16,
                    "block_ids": [
                        "b_match",
                        "b_picmatch",
                        "b_gap",
                        "b_choice",
                        "b_tf",
                        "b_sort",
                        "b_rule",
                        "b_gal",
                        "b_grid",
                    ],
                },
                {
                    "id": "s_prod",
                    "phase": "production",
                    "title": "Production: questions",
                    "minutes": 9,
                    "block_ids": ["b_qset"],
                },
                {
                    "id": "s_wrap",
                    "phase": "wrap_up",
                    "title": "Wrap-up",
                    "minutes": 3,
                    "block_ids": ["b_self", "b_sum"],
                },
            ],
            "blocks": blocks,
        }
    )
