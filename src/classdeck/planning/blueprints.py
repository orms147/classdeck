"""One blueprint per textbook lesson type of a secondary unit (Global Success 6-9).

``docs/secondary-units.md``, "Lesson blueprints". A lesson is not cut from a generic skeleton:
its type (Getting Started, A Closer Look 1 ...) fixes the phases, which textbook parts go where,
what the model may add and what it must not, and how many slides the deck should have. The
section banner on the page selects the blueprint; ``planning/secondary.py`` applies it (phases of
the exercises, the AI-added parts, the summary) and :func:`blueprint_prompt` gives the model its
share.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from classdeck.lesson.schema import Phase


@dataclass(frozen=True)
class Blueprint:
    """The plan of one lesson type."""

    number: int
    name: str
    budget: tuple[int, int]
    """Pupil slides, excluding dividers and teacher-only slides (the minimum and the maximum)."""
    weights: dict[Phase, int]
    """The 45-minute skeleton: minutes per phase (a phase with nothing in it gives them away)."""
    warm_up: str
    stages: tuple[str, ...]
    production: str
    wrap_up: str
    summary: str
    """What the summary slide holds (the recap of this lesson type)."""
    may_add: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    new_words: bool = False
    """Whether the lesson shows the new words as flashcards (types 1 and 2)."""
    warm_pictures: bool = False
    """Whether the warm-up shows the page's pictures that no exercise uses."""
    extras: tuple[str, ...] = field(default=())
    """Names of the AI-added parts of the reply this type asks for (``speaking_frame`` ...)."""


_COMMON_AVOID = (
    "rewrite or reorder the textbook's exercises, or add an exercise the page does not have",
    "introduce language that is not on the unit's pages in anything pupils read",
    "write Vietnamese anywhere except the glosses (meaning_vi and the reading glossary)",
)

BLUEPRINTS: dict[int, Blueprint] = {
    1: Blueprint(
        number=1,
        name="Getting Started",
        budget=(14, 20),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 12,
            Phase.PRACTICE: 15,
            Phase.PRODUCTION: 9,
            Phase.WRAP_UP: 4,
        },
        warm_up=(
            "a topic hook: 2 or 3 questions about the unit topic that the pupils can answer with "
            "what they already know, with the picture of the textbook page as the prompt; no new "
            "language, and it leads into the conversation without giving its content away"
        ),
        stages=(
            "the conversation (listen and read, line by line)",
            "the new words from the conversation as flashcards",
            "the comprehension and vocabulary exercises, in book order",
        ),
        production="the page's quiz or personal questions (it has its own slide)",
        wrap_up="recap the new words, then set the homework",
        summary="the new words",
        new_words=True,
        warm_pictures=True,
        may_add=("the warm-up", "the new words' gloss and IPA", "answer keys", "teacher steps"),
        must_not=("a second conversation", "grammar explanations"),
    ),
    2: Blueprint(
        number=2,
        name="A Closer Look 1",
        budget=(12, 18),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 8,
            Phase.PRACTICE: 20,
            Phase.PRODUCTION: 8,
            Phase.WRAP_UP: 4,
        },
        warm_up=(
            "a quick review game with the words of the previous lesson (the unit topic); pupils "
            "use words they already know, no new language"
        ),
        stages=(
            "vocabulary: the new words presented as flashcards (with the page's pictures)",
            "the vocabulary exercises (matching, word-picture, gap fill), in book order",
            "pronunciation: the sound sort, the words with the speaker button, the sentences",
        ),
        production=(
            "the page's 'What other ... can you add?' prompt or a quick personal-use task with "
            "the words of the lesson"
        ),
        wrap_up="recap the words and the sounds of the lesson",
        summary="the new words and the sounds",
        new_words=True,
        may_add=("the warm-up", "the words' gloss and IPA", "answer keys", "teacher steps"),
        must_not=("grammar explanations", "reading texts"),
    ),
    3: Blueprint(
        number=3,
        name="A Closer Look 2",
        budget=(12, 18),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 8,
            Phase.PRACTICE: 20,
            Phase.PRODUCTION: 9,
            Phase.WRAP_UP: 3,
        },
        warm_up=(
            "a sentence game with the unit topic: pupils make simple sentences about it with "
            "language they already know; it does not use the grammar point of the lesson"
        ),
        stages=(
            "grammar: the Remember! rule box with its examples, one slide per rule",
            "the controlled practice of that rule, in book order",
            "the second grammar point the same way (its rule box, then its practice)",
        ),
        production=(
            "the page's game (for example 'Find someone who ...'); when the page has none, a "
            "short personalised speaking task with the grammar of the lesson"
        ),
        wrap_up="recap each rule with one example",
        summary="each rule with one example",
        may_add=("the warm-up", "answer keys", "teacher steps", "a speaking task if no game"),
        must_not=("a grammar rule the page does not have", "new vocabulary lists"),
    ),
    4: Blueprint(
        number=4,
        name="Communication",
        budget=(12, 18),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 10,
            Phase.PRACTICE: 15,
            Phase.PRODUCTION: 11,
            Phase.WRAP_UP: 4,
        },
        warm_up=(
            "situation pictures or a spoken situation and the question 'What would you say?'; "
            "pupils answer with what they know, no new language"
        ),
        stages=(
            "Everyday English: the model exchanges as a conversation, the highlighted phrases "
            "as chips",
            "pair practice: the situations as cards (pupils use the highlighted phrases)",
            "culture / places: the pictures, the questions, the listening task (the teacher "
            "plays the book audio)",
        ),
        production="the pair interview from the page",
        wrap_up="recap the useful phrases of the Everyday English part",
        summary="the useful phrases",
        warm_pictures=True,
        may_add=("the warm-up", "answer keys", "teacher steps", "a pair task if the page has none"),
        must_not=("new functional phrases the page does not have",),
    ),
    5: Blueprint(
        number=5,
        name="Skills 1",
        budget=(12, 18),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 12,
            Phase.PRACTICE: 12,
            Phase.PRODUCTION: 12,
            Phase.WRAP_UP: 4,
        },
        warm_up="a prediction task from the pictures and the title of the reading text",
        stages=(
            "reading: the pre-reading questions, then the text as a reading slide (paragraphs "
            "with the highlighted words and their glosses)",
            "the vocabulary match of the highlighted words, then the comprehension exercises",
        ),
        production=(
            "speaking: the page's question cards, then a presentation frame (sentence starters "
            "and a timer) for the short presentation"
        ),
        wrap_up="2 or 3 key ideas of the text and the speaking task",
        summary="2 or 3 key ideas",
        may_add=(
            "the warm-up",
            "answer keys",
            "teacher steps",
            "the reading glossary",
            "speaking_frame: sentence starters for the presentation",
        ),
        must_not=("another reading text", "comprehension questions the page does not have"),
        extras=("speaking_frame",),
    ),
    6: Blueprint(
        number=6,
        name="Skills 2",
        budget=(10, 16),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRESENTATION: 5,
            Phase.PRACTICE: 13,
            Phase.PRODUCTION: 18,
            Phase.WRAP_UP: 4,
        },
        warm_up="the pictures and the questions who / what / where; pupils guess the topic",
        stages=(
            "listening: the pre-listening discussion, then the tasks (gap fill, true / false); "
            "the teacher plays the book audio and the answers are in teacher mode",
        ),
        production=(
            "writing: the guiding questions, then a paragraph frame (the starter sentence, "
            "linking words, the word count) and a short model paragraph"
        ),
        wrap_up="a checklist for the paragraph",
        summary="a checklist for the paragraph",
        warm_pictures=True,
        may_add=(
            "the warm-up",
            "answer keys",
            "teacher steps",
            "writing_frame: linking words and the word count",
            "model_paragraph: a short model paragraph (AI-added, labelled)",
        ),
        must_not=("a listening script the pupils can read", "a second writing task"),
        extras=("writing_frame", "model_paragraph"),
    ),
    7: Blueprint(
        number=7,
        name="Looking Back & Project",
        budget=(12, 18),
        weights={
            Phase.WARM_UP: 5,
            Phase.PRACTICE: 18,
            Phase.PRODUCTION: 18,
            Phase.WRAP_UP: 4,
        },
        warm_up="a quick-fire word quiz from the whole unit (the words of the unit's pages)",
        stages=(
            "review: the vocabulary and grammar exercises in book order, each on its own slide",
        ),
        production=(
            "the project: one slide with the task, the steps as numbered cards, the page's tables "
            "as a fill-in grid, the pictures as one gallery slide; then the Now I can self-check"
        ),
        wrap_up="a recap of the unit (what the pupils can now do)",
        summary="what the pupils can now do",
        may_add=("the warm-up", "answer keys", "teacher steps", "the project's teacher steps"),
        must_not=("a new exercise", "a new project"),
    ),
}


def blueprint_for(lesson_no: int) -> Blueprint:
    """The blueprint of textbook lesson ``lesson_no`` (1-7)."""
    return BLUEPRINTS[lesson_no]


def blueprint_prompt(blueprint: Blueprint) -> str:
    """The blueprint as the drafting prompt shows it to the model."""
    lines = [
        f"LESSON TYPE: {blueprint.name} (lesson {blueprint.number} of the unit)",
        f"Warm-up: {blueprint.warm_up}.",
        "Main stages, in this order:",
        *(f"  {n}. {stage}" for n, stage in enumerate(blueprint.stages, start=1)),
        f"Production: {blueprint.production}.",
        f"Wrap-up: {blueprint.wrap_up}.",
        "You may add only: " + "; ".join(blueprint.may_add) + ".",
        "You must not: " + "; ".join((*blueprint.must_not, *_COMMON_AVOID)) + ".",
    ]
    return "\n".join(lines)
