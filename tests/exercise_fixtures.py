"""A synthetic lesson with one block of every interactive exercise kind (and a long one)."""

from __future__ import annotations

from typing import Any

from classdeck.lesson import Lesson

READING: dict[str, Any] = {
    "id": "e_read",
    "kind": "reading",
    "content": {
        "exercise_no": "1",
        "instruction": "Read the text about city life.",
        "title": "Life in a big city",
        "paragraphs": [
            {
                "text": (
                    "Mai lives in a busy city. Every morning she takes the bus to school, "
                    "and the streets are crowded with people and traffic."
                ),
                "highlight": ["busy", "crowded"],
            },
            {
                "text": (
                    "In the evening she visits her grandparents. They live in a quiet "
                    "village near the river, so the air is fresh."
                ),
                "highlight": ["quiet"],
            },
        ],
        "glossary": {"busy": "full of activity", "crowded": "too many people", "quiet": "calm"},
    },
    "sources": [],
    "provenance": "generated",
}

RULE_BOX: dict[str, Any] = {
    "id": "e_rule",
    "kind": "rule_box",
    "content": {
        "title": "Remember!",
        "points": [
            "Use the present simple for habits and facts.",
            "Add -s or -es to the verb after he, she and it.",
        ],
        "examples": ["She takes the bus every day.", "They live near the river."],
    },
    "sources": [],
    "provenance": "generated",
}

MATCH: dict[str, Any] = {
    "id": "e_match",
    "kind": "match",
    "content": {
        "exercise_no": "2",
        "instruction": "Match the words with their meanings.",
        "left": ["busy", "quiet", "crowded", "fresh", "traffic"],
        "right": [
            "calm, with little noise",
            "cars and buses on the road",
            "full of people",
            "clean and cool",
            "full of activity",
            "very large",
        ],
        "answer": {"0": 4, "1": 0, "2": 2, "3": 3, "4": 1},
    },
    "sources": [],
    "provenance": "generated",
}

GAP_FILL: dict[str, Any] = {
    "id": "e_gap",
    "kind": "gap_fill",
    "content": {
        "exercise_no": "3",
        "instruction": "Complete the sentences with the words in the box.",
        "word_box": ["takes", "lives", "visit", "are", "goes"],
        "items": [
            {"text": "Mai ___ the bus to school.", "answer": "takes"},
            {"text": "Her grandfather ___ in a village.", "answer": "lives"},
            {"text": "We ___ our grandparents on Sundays.", "answer": "visit"},
            {"text": "The streets ___ very crowded.", "answer": "are"},
        ],
    },
    "sources": [],
    "provenance": "generated",
}

CHOICE: dict[str, Any] = {
    "id": "e_choice",
    "kind": "choice",
    "content": {
        "exercise_no": "4",
        "instruction": "Choose the correct answer.",
        "items": [
            {
                "stem": "She ___ to school by bus.",
                "options": ["go", "goes", "going"],
                "answer": "goes",
            },
            {
                "stem": "They ___ in a quiet village.",
                "options": ["live", "lives", "living"],
                "answer": "live",
            },
            {
                "stem": "My father ___ at six.",
                "options": ["wake up", "wakes up", "waking up"],
                "answer": "wakes up",
            },
            {
                "stem": "I ___ my homework every night.",
                "options": ["do", "does", "doing"],
                "answer": "do",
            },
        ],
    },
    "sources": [],
    "provenance": "generated",
}

TRUE_FALSE: dict[str, Any] = {
    "id": "e_tf",
    "kind": "true_false",
    "content": {
        "exercise_no": "5",
        "instruction": "Read the text again. Are the sentences true or false?",
        "items": [
            {"statement": "Mai goes to school by bus.", "answer": True},
            {"statement": "Her grandparents live in the city.", "answer": False},
            {"statement": "The village is near a river.", "answer": True},
        ],
    },
    "sources": [],
    "provenance": "generated",
}

LONG_GAP: dict[str, Any] = {
    "id": "e_long",
    "kind": "gap_fill",
    "content": {
        "exercise_no": "6",
        "instruction": "Write the correct form of the verbs.",
        "items": [
            {"text": f"Sentence {n}: he ___ every day.", "answer": f"word{n}"} for n in range(1, 9)
        ],
    },
    "sources": [],
    "provenance": "generated",
}

QUESTIONS: dict[str, Any] = {
    "id": "e_q",
    "kind": "question_set",
    "content": {
        "exercise_no": "7",
        "instruction": "Work in pairs. Ask and answer.",
        "questions": [
            {"question": "How do you go to school?", "answer": "I go to school by bike."},
            {"question": "Do you live in a city or a village?", "answer": "I live in a village."},
            {"question": "What do you do in the evening?"},
        ],
    },
    "sources": [],
    "provenance": "generated",
}


def make_exercise_lesson() -> Lesson:
    blocks = [READING, RULE_BOX, MATCH, GAP_FILL, CHOICE, TRUE_FALSE, LONG_GAP, QUESTIONS]
    return Lesson.model_validate(
        {
            "id": "l_exercises",
            "title": "Unit 3 City life - Skills 1",
            "grade": 5,
            "curriculum": {"version": "gdpt-2018", "unit_id": "ta5.u3", "lesson_no": 5},
            "sources": [],
            "sections": [
                {
                    "id": "s_pres",
                    "phase": "presentation",
                    "title": "Presentation: Reading",
                    "minutes": 12,
                    "block_ids": ["e_read", "e_rule"],
                },
                {
                    "id": "s_prac",
                    "phase": "practice",
                    "title": "Practice: Exercises",
                    "minutes": 25,
                    "block_ids": ["e_match", "e_gap", "e_choice", "e_tf", "e_long"],
                },
                {
                    "id": "s_prod",
                    "phase": "production",
                    "title": "Production: Speaking",
                    "minutes": 8,
                    "block_ids": ["e_q"],
                },
            ],
            "blocks": {block["id"]: block for block in blocks},
        }
    )
