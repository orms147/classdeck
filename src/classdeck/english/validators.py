"""Deterministic level checks for a lesson: word list, grammar scope, skills, IPA, timing.

Every validator takes the lesson and its curriculum and returns :data:`CheckOutcome` pairs
``(block_id, result)``; ``block_id`` is ``None`` for a lesson-level result. Nothing here calls a
model or mutates its input: :func:`run_all` returns a new lesson with the results applied, and
a failed check is data on the lesson, not an exception. See ``docs/english-rules.md``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import NamedTuple, Protocol

import simplemma

from classdeck.curriculum.loader import Curriculum
from classdeck.curriculum.models import GrammarRule
from classdeck.english.rules import PERIOD_MINUTES_TOLERANCE
from classdeck.lesson.schema import (
    ActivityBlock,
    Block,
    BlockKind,
    CheckResult,
    CheckStatus,
    DialogueBlock,
    Lesson,
    ObjectiveBlock,
    Phase,
    PhonicsBlock,
    SentencePatternBlock,
    SourceDocument,
    SummaryBlock,
    VocabularyBlock,
)

CheckOutcome = tuple[str | None, CheckResult]
"""``(block_id, result)``; a ``None`` block id means the result belongs to the lesson."""

Sources = Mapping[str, SourceDocument]

VOCAB_LEVEL = "english.vocab_level"
GRAMMAR_SCOPE = "english.grammar_scope"
SKILL_COVERAGE = "english.skill_coverage"
IPA_PRESENT = "english.ipa_present"
PATTERN_USED = "english.pattern_used"
STEPS_PRESENT = "english.steps_present"
TIMING = "english.timing"
OUTCOME_COVERED = "curriculum.outcome_covered"

OWNED_CHECKS: frozenset[str] = frozenset(
    {
        VOCAB_LEVEL,
        GRAMMAR_SCOPE,
        SKILL_COVERAGE,
        IPA_PRESENT,
        PATTERN_USED,
        STEPS_PRESENT,
        TIMING,
        OUTCOME_COVERED,
    }
)
"""Names of the results this module produces; :func:`run_all` replaces all of them."""

VOCAB_LEVEL_KINDS: frozenset[BlockKind] = frozenset(
    {
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.DIALOGUE,
        BlockKind.ACTIVITY,
        BlockKind.SUMMARY,
    }
)
"""Block kinds ``english.vocab_level`` checks."""

GRAMMAR_KINDS: frozenset[BlockKind] = frozenset(
    {BlockKind.SENTENCE_PATTERN, BlockKind.DIALOGUE, BlockKind.ACTIVITY}
)
"""Block kinds ``english.grammar_scope`` checks."""
_PATTERN_PHASES = frozenset({Phase.PRACTICE, Phase.PRODUCTION})
_SKILL_ORDER = ("listening", "speaking", "reading", "writing")

FUNCTION_WORDS: frozenset[str] = frozenset(
    """
    a an the and or but then so
    i me you he him she her it we us they them my your his its our their mine yours
    this that these those each every some any all
    be am is are was were been being have has had do does did
    in on at to of for with from by up down into out over under about after before between
    behind near next above below through around across off along here there
    please ok okay let let's
    """.split()  # noqa: SIM905
)
"""Function words that are never reported by ``english.vocab_level``."""


# --- text extraction ---------------------------------------------------------------------------


def _clean(texts: Iterable[str | None]) -> list[str]:
    return [text.strip() for text in texts if text and text.strip()]


def _without_slots(pattern: str) -> str:
    text = re.sub(r"\s*_{2,}\s*", " ", pattern).strip()
    return re.sub(r"\s+([.,!?;:])", r"\1", text)


def english_text_of(block: Block) -> list[str]:
    """The English strings a block carries, as the level checks should read them.

    Vietnamese glosses (``meaning_vi``) and teacher notes are left out; blocks with no
    checkable English (objectives, teacher notes, image references) yield an empty list.
    """
    match block:
        case VocabularyBlock():
            items = block.content.items
            return _clean([*(item.word for item in items), *(item.example for item in items)])
        case SentencePatternBlock():
            pattern = block.content
            return _clean([_without_slots(pattern.pattern), *pattern.examples])
        case DialogueBlock():
            return _clean(line.text for line in block.content.lines)
        case PhonicsBlock():
            return _clean([*block.content.words, *block.content.chant_lines])
        case ActivityBlock():
            task = block.content
            return _clean(
                [
                    task.title,
                    task.instructions,
                    *task.teacher_steps,
                    *task.student_steps,
                    *task.materials,
                ]
            )
        case SummaryBlock():
            return _clean(block.content.bullets)
        case _:
            return []


def pupil_text_of(block: Block) -> list[str]:
    """The part of :func:`english_text_of` that pupils see or hear.

    The level checks read only this. For an activity that is the ``instructions`` and the
    ``student_steps``; its title, teacher steps and materials are written for the teacher.
    Every other kind is entirely pupil-facing.
    """
    if isinstance(block, ActivityBlock):
        task = block.content
        return _clean([task.instructions, *task.student_steps])
    return english_text_of(block)


# --- tokenisation ------------------------------------------------------------------------------

_SLOT = re.compile(r"_+")
_APOSTROPHES = "'\N{RIGHT SINGLE QUOTATION MARK}"
_WORD = re.compile(rf"\w+(?:[{_APOSTROPHES}]\w+)*")
_CONTRACTION = re.compile(
    rf"^(\w+?)(n[{_APOSTROPHES}]t|[{_APOSTROPHES}](?:m|s|re|ve|ll|d))$", re.IGNORECASE
)
_NEGATED_STEMS = {"ca": "can", "wo": "will", "sha": "shall"}
_OPENING = frozenset(".!?\"'([{\N{LEFT SINGLE QUOTATION MARK}\N{LEFT DOUBLE QUOTATION MARK}")


class Token(NamedTuple):
    surface: str
    """Lower-cased word, without any contraction suffix (``I'm`` gives ``i``)."""
    proper: bool
    """Capitalised and not at the start of a sentence, so probably a name."""


def tokenize(text: str) -> list[Token]:
    """Words of ``text`` for level checking; numbers, slots and punctuation are dropped.

    Contraction suffixes (``'m``, ``'s``, ``'re``, ``n't`` ...) are function pieces and are
    not returned; the word they attach to is.
    """
    text = _SLOT.sub(" ", text)
    tokens: list[Token] = []
    for match in _WORD.finditer(text):
        raw = match.group()
        if any(char.isdigit() for char in raw):
            continue
        base = raw
        contraction = _CONTRACTION.match(raw)
        if contraction:
            base = contraction.group(1)
            if contraction.group(2).lower().startswith("n"):
                base = _NEGATED_STEMS.get(base.lower(), base)
        before = text[: match.start()].rstrip()
        sentence_start = not before or before[-1] in _OPENING
        proper = base[:1].isupper() and base != "I" and not sentence_start
        tokens.append(Token(base.lower(), proper))
    return tokens


@lru_cache(maxsize=8192)
def _lemma(word: str) -> str:
    return simplemma.lemmatize(word, lang="en").lower()


def _lemmas(word: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((word, _lemma(word))))


@lru_cache(maxsize=8192)
def _candidate_forms(word: str) -> tuple[str, ...]:
    """The word, its lemma and plain suffix-stripped stems, to cover lemmatiser gaps."""
    forms = list(_lemmas(word))
    for suffix, restore in (
        ("ing", ""),
        ("ing", "e"),
        ("ed", ""),
        ("ed", "e"),
        ("es", ""),
        ("s", ""),
    ):
        if word.endswith(suffix) and len(word) >= len(suffix) + 3:
            stem = word[: -len(suffix)]
            forms.append(stem + restore)
            if stem[-1] == stem[-2]:
                forms.append(stem[:-1] + restore)
    if word.endswith("ies") and len(word) > 4:
        forms.append(word[:-3] + "y")
    return tuple(dict.fromkeys(forms))


def word_forms(word: str) -> tuple[str, ...]:
    """The word, its lemma and plain suffix-stripped stems: forms a matcher may compare."""
    return _candidate_forms(word.lower())


def _forms_of_texts(texts: Iterable[str]) -> set[str]:
    forms: set[str] = set()
    for text in texts:
        for token in tokenize(text):
            forms.update(_lemmas(token.surface))
    return forms


# --- helpers -----------------------------------------------------------------------------------


def _outcome(
    block_id: str | None, name: str, status: CheckStatus, detail: str | None = None
) -> CheckOutcome:
    return block_id, CheckResult(name=name, status=status, detail=detail)


def _blocks(lesson: Lesson) -> list[Block]:
    """Blocks in lesson order, followed by any block not placed in a section."""
    ordered = lesson.ordered_blocks()
    placed = {block.id for block in ordered}
    return [*ordered, *(block for key, block in lesson.blocks.items() if key not in placed)]


def _skill_names(skills: Iterable[object]) -> set[str]:
    return {str(skill) for skill in skills}


def _sort_skills(skills: Iterable[str]) -> list[str]:
    return sorted(
        skills, key=lambda name: (_SKILL_ORDER.index(name) if name in _SKILL_ORDER else 99, name)
    )


def _word_list(lesson: Lesson, curriculum: Curriculum) -> frozenset[str]:
    try:
        return curriculum.word_list(lesson.curriculum.unit_id)
    except KeyError:
        return curriculum.word_list()


# --- english.vocab_level -----------------------------------------------------------------------


def _cited_text(block: Block, sources: Sources | None) -> list[str]:
    if not sources:
        return []
    texts: list[str] = []
    for citation in block.sources:
        document = sources.get(citation.doc_id)
        span = document.find_span(citation.page, citation.span_id) if document else None
        if span is not None and span.text.strip():
            texts.append(span.text)
    return texts


def allowed_forms(lesson: Lesson, curriculum: Curriculum) -> frozenset[str]:
    """The word forms ``english.vocab_level`` accepts in every block of the lesson.

    The lemma forms of the lesson's word list (grade list, classroom language, unit words) plus
    the function words; a block's own cited spans are added per block by :func:`vocab_offenders`.
    """
    return _forms_of_texts(_word_list(lesson, curriculum)) | FUNCTION_WORDS


def vocab_offenders(
    block: Block, allowed: frozenset[str], sources: Sources | None = None
) -> list[str]:
    """Sorted pupil-facing words of ``block`` outside ``allowed``, its cited spans, names and
    numbers; the words ``english.vocab_level`` reports for the block."""
    accepted = allowed | _forms_of_texts(_cited_text(block, sources))
    tokens = [t for text in pupil_text_of(block) for t in tokenize(text) if not t.proper]
    # Single letters ("b", "h") and IPA fragments ("tʃ") are phonics material, not vocabulary.
    words = {t.surface for t in tokens if len(t.surface) > 1 and t.surface.isascii()}
    return sorted(
        word for word in words if not any(form in accepted for form in _candidate_forms(word))
    )


def vocab_level(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Pupil-facing words must be in the grade list, a cited span, a name or a function word."""
    base_allowed = allowed_forms(lesson, curriculum)
    outcomes: list[CheckOutcome] = []
    for block in _blocks(lesson):
        if block.kind not in VOCAB_LEVEL_KINDS:
            continue
        offenders = vocab_offenders(block, base_allowed, sources)
        if offenders:
            detail = f"not in the grade word list or cited spans: {', '.join(offenders)}"
            outcomes.append(_outcome(block.id, VOCAB_LEVEL, CheckStatus.WARN, detail))
        else:
            texts = pupil_text_of(block)
            words = {t.surface for text in texts for t in tokenize(text) if not t.proper}
            detail = f"{len(words)}/{len(words)} words in the grade word list or cited spans"
            outcomes.append(_outcome(block.id, VOCAB_LEVEL, CheckStatus.PASS, detail))
    return outcomes


# --- english.grammar_scope ---------------------------------------------------------------------


def grammar_hits(block: Block, rules: Iterable[GrammarRule]) -> list[str]:
    """The ``rule id: 'snippet'`` hits of the forbidden rules on the block's pupil-facing text."""
    texts = pupil_text_of(block)
    hits: list[str] = []
    for rule in rules:
        for text in texts:
            found = rule.regex.search(text)
            if found:
                hit = f"{rule.id}: '{' '.join(found.group(0).split())}'"
                if hit not in hits:
                    hits.append(hit)
    return hits


def grammar_scope(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """No pupil-facing text may match a ``grammar_scope.forbidden`` rule of the curriculum."""
    rules = curriculum.forbidden_rules()
    outcomes: list[CheckOutcome] = []
    for block in _blocks(lesson):
        if block.kind not in GRAMMAR_KINDS:
            continue
        hits = grammar_hits(block, rules)
        if hits:
            detail = f"outside the grade's grammar scope - {'; '.join(hits)}"
            outcomes.append(_outcome(block.id, GRAMMAR_SCOPE, CheckStatus.WARN, detail))
        else:
            outcomes.append(_outcome(block.id, GRAMMAR_SCOPE, CheckStatus.PASS))
    return outcomes


# --- english.skill_coverage --------------------------------------------------------------------


def _outcome_skills(curriculum: Curriculum, outcome_id: str) -> set[str] | None:
    try:
        return _skill_names(curriculum.outcome(outcome_id).skills)
    except KeyError:
        return None


def skill_coverage(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Every skill named by the period's outcomes needs at least one activity."""
    required: set[str] = set()
    for outcome_id in lesson.curriculum.outcome_ids:
        required |= _outcome_skills(curriculum, outcome_id) or set()
    present: set[str] = set()
    for block in lesson.blocks.values():
        if isinstance(block, ActivityBlock):
            present |= _skill_names(block.content.skills)
    missing = required - present
    if missing:
        detail = f"no activity practises: {', '.join(_sort_skills(missing))}"
        return [_outcome(None, SKILL_COVERAGE, CheckStatus.WARN, detail)]
    detail = f"covered: {', '.join(_sort_skills(required))}" if required else "no skills required"
    return [_outcome(None, SKILL_COVERAGE, CheckStatus.PASS, detail)]


# --- english.ipa_present -----------------------------------------------------------------------


@lru_cache(maxsize=1)
def ipa_dictionary() -> Mapping[str, str]:
    """The bundled pronunciation list: lower-cased word to British IPA, slashes included."""
    resource = resources.files("classdeck.english").joinpath("data", "ipa_core.json")
    raw = json.loads(resource.read_text(encoding="utf-8"))
    return MappingProxyType({k.lower(): v for k, v in raw.items() if not k.startswith("_")})


def lookup_ipa(word: str) -> str | None:
    """IPA for a word, or for a phrase whose every word is known; ``None`` otherwise."""
    dictionary = ipa_dictionary()
    key = word.strip().lower()
    if key in dictionary:
        return dictionary[key]
    parts = re.findall(r"[^\W\d_]+", key)
    if len(parts) > 1 and all(part in dictionary for part in parts):
        return "/" + " ".join(dictionary[part].strip("/") for part in parts) + "/"
    return None


def fill_ipa(lesson: Lesson) -> Lesson:
    """A copy of the lesson with ``ipa`` filled from the bundled list where it is missing."""
    result = lesson.model_copy(deep=True)
    for block in result.blocks.values():
        if isinstance(block, VocabularyBlock):
            for item in block.content.items:
                if not (item.ipa and item.ipa.strip()):
                    item.ipa = lookup_ipa(item.word) or item.ipa
    return result


def ipa_present(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Report vocabulary items that still have no IPA (run :func:`fill_ipa` first)."""
    outcomes: list[CheckOutcome] = []
    for block in _blocks(lesson):
        if not isinstance(block, VocabularyBlock):
            continue
        missing = [item.word for item in block.content.items if not (item.ipa and item.ipa.strip())]
        if missing:
            detail = f"no IPA for: {', '.join(missing)}"
            outcomes.append(_outcome(block.id, IPA_PRESENT, CheckStatus.WARN, detail))
        else:
            outcomes.append(_outcome(block.id, IPA_PRESENT, CheckStatus.PASS))
    return outcomes


# --- english.pattern_used ----------------------------------------------------------------------


def _literal_regex(piece: str) -> str:
    words = [re.escape(word).replace("'", f"[{_APOSTROPHES}]") for word in piece.split()]
    body = r"\s+".join(words)
    lead = r"\s+" if piece[:1].isspace() else ""
    trail = r"\s+" if piece[-1:].isspace() else ""
    return f"{lead}{body}{trail}"


def pattern_regex(pattern: str) -> re.Pattern[str] | None:
    """A case-insensitive regex for a sentence pattern, each ``___`` slot matching any text.

    Alternatives written as ``A / B`` match either side; trailing ``. ? !`` are optional. A
    pattern with no words besides slots gives ``None``.
    """
    alternatives: list[str] = []
    for part in re.split(r"\s+/\s+", pattern):
        core = part.strip().rstrip(".?!").strip()
        if not re.search(r"[^\W\d_]", core):
            continue
        pieces = re.split(r"_{2,}", core)
        body = ".+?".join(_literal_regex(piece) for piece in pieces)
        start = r"(?<!\w)" if pieces[0].strip() else ""
        end = r"(?!\w)" if pieces[-1].strip() else ""
        alternatives.append(f"{start}{body}{end}")
    if not alternatives:
        return None
    return re.compile("|".join(f"(?:{alternative})" for alternative in alternatives), re.IGNORECASE)


def patterns_of(lesson: Lesson) -> list[tuple[SentencePatternBlock, re.Pattern[str]]]:
    """Each ``sentence_pattern`` block of the lesson with its regex, in lesson order."""
    found: list[tuple[SentencePatternBlock, re.Pattern[str]]] = []
    for block in _blocks(lesson):
        if isinstance(block, SentencePatternBlock):
            regex = pattern_regex(block.content.pattern)
            if regex is not None:
                found.append((block, regex))
    return found


def pattern_used(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Practice and production activities must use a pattern taught in the lesson."""
    patterns = patterns_of(lesson)
    outcomes: list[CheckOutcome] = []
    for block in _blocks(lesson):
        if not isinstance(block, ActivityBlock):
            continue
        section = lesson.section_of(block.id)
        if section is None or section.phase not in _PATTERN_PHASES:
            continue
        texts = english_text_of(block)
        used = [
            f"{pattern_block.id}: {pattern_block.content.pattern}"
            for pattern_block, regex in patterns
            if any(regex.search(text) for text in texts)
        ]
        if used:
            detail = f"uses pattern from {'; '.join(used)}"
            outcomes.append(_outcome(block.id, PATTERN_USED, CheckStatus.PASS, detail))
        elif patterns:
            detail = "does not use any sentence pattern of the lesson"
            outcomes.append(_outcome(block.id, PATTERN_USED, CheckStatus.WARN, detail))
        else:
            detail = "the lesson has no sentence_pattern block to practise"
            outcomes.append(_outcome(block.id, PATTERN_USED, CheckStatus.WARN, detail))
    return outcomes


# --- english.steps_present ---------------------------------------------------------------------


def steps_present(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Every activity names a skill and has both teacher and pupil steps."""
    outcomes: list[CheckOutcome] = []
    for block in _blocks(lesson):
        if not isinstance(block, ActivityBlock):
            continue
        content = block.content
        missing = [
            label
            for label, present in (
                ("skills", bool(content.skills)),
                ("teacher_steps", bool(_clean(content.teacher_steps))),
                ("student_steps", bool(_clean(content.student_steps))),
            )
            if not present
        ]
        if missing:
            detail = f"missing: {', '.join(missing)}"
            outcomes.append(_outcome(block.id, STEPS_PRESENT, CheckStatus.FAIL, detail))
        else:
            outcomes.append(_outcome(block.id, STEPS_PRESENT, CheckStatus.PASS))
    return outcomes


# --- english.timing ----------------------------------------------------------------------------


def timing(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Section minutes add up to the period length, within the tolerance."""
    total = sum(section.minutes for section in lesson.sections)
    expected = curriculum.period_minutes
    if abs(total - expected) <= PERIOD_MINUTES_TOLERANCE:
        return [_outcome(None, TIMING, CheckStatus.PASS, f"{total} min")]
    detail = f"{total} min; expected {expected} +/- {PERIOD_MINUTES_TOLERANCE}"
    return [_outcome(None, TIMING, CheckStatus.WARN, detail)]


# --- curriculum.outcome_covered ----------------------------------------------------------------


def outcome_covered(
    lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
) -> list[CheckOutcome]:
    """Each outcome needs an objective that names it and an activity that practises its skills."""
    referenced = {
        outcome_id
        for block in lesson.blocks.values()
        if isinstance(block, ObjectiveBlock)
        for outcome_id in block.content.outcome_ids
    }
    activities = [b for b in lesson.blocks.values() if isinstance(b, ActivityBlock)]
    problems: list[str] = []
    for outcome_id in lesson.curriculum.outcome_ids:
        skills = _outcome_skills(curriculum, outcome_id)
        reasons: list[str] = []
        if skills is None:
            reasons.append("unknown outcome")
        else:
            if outcome_id not in referenced:
                reasons.append("no objective")
            practised = any(
                not skills or skills & _skill_names(activity.content.skills)
                for activity in activities
            )
            if not practised:
                reasons.append("no activity")
        if reasons:
            problems.append(f"{outcome_id} ({', '.join(reasons)})")
    if problems:
        detail = f"not covered: {'; '.join(problems)}"
        return [_outcome(None, OUTCOME_COVERED, CheckStatus.WARN, detail)]
    count = len(lesson.curriculum.outcome_ids)
    return [_outcome(None, OUTCOME_COVERED, CheckStatus.PASS, f"{count} outcome(s) covered")]


# --- running -----------------------------------------------------------------------------------


class Validator(Protocol):
    def __call__(
        self, lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None
    ) -> list[CheckOutcome]: ...


ALL_VALIDATORS: list[Validator] = [
    vocab_level,
    grammar_scope,
    skill_coverage,
    ipa_present,
    pattern_used,
    steps_present,
    timing,
    outcome_covered,
]


def _upsert(checks: list[CheckResult], result: CheckResult) -> None:
    """Replace the result with the same name (dropping duplicates of it), else append."""
    indexes = [index for index, existing in enumerate(checks) if existing.name == result.name]
    if not indexes:
        checks.append(result)
        return
    checks[indexes[0]] = result
    for index in reversed(indexes[1:]):
        del checks[index]


def apply_outcomes(lesson: Lesson, outcomes: Iterable[CheckOutcome]) -> Lesson:
    """A copy of the lesson with the outcomes stored, replacing results of the same name."""
    result = lesson.model_copy(deep=True)
    for block_id, check in outcomes:
        if block_id is None:
            target = result.checks
        elif block_id in result.blocks:
            target = result.blocks[block_id].checks
        else:
            raise KeyError(f"check {check.name!r} refers to unknown block {block_id!r}")
        _upsert(target, check.model_copy())
    return result


def run_all(lesson: Lesson, curriculum: Curriculum, *, sources: Sources | None = None) -> Lesson:
    """Fill missing IPA, run every validator and store the results on a new lesson.

    Results of the validators are replaced wholesale, so a check that no longer applies to a
    block (for example after it moved to another phase) does not linger; results from other
    stages, such as ``grounding.*``, are kept.
    """
    filled = fill_ipa(lesson)
    outcomes: list[CheckOutcome] = []
    for validator in ALL_VALIDATORS:
        outcomes.extend(validator(filled, curriculum, sources=sources))
    filled.checks = [check for check in filled.checks if check.name not in OWNED_CHECKS]
    for block in filled.blocks.values():
        block.checks = [check for check in block.checks if check.name not in OWNED_CHECKS]
    return apply_outcomes(filled, outcomes)


def mark_stale(lesson: Lesson, block_ids: Iterable[str]) -> Lesson:
    """A copy with every check on the given blocks set to ``stale`` (unknown ids are ignored)."""
    result = lesson.model_copy(deep=True)
    for block_id in block_ids:
        block = result.blocks.get(block_id)
        if block is not None:
            block.checks = [
                check.model_copy(update={"status": CheckStatus.STALE}) for check in block.checks
            ]
    return result


def has_failures(lesson: Lesson) -> bool:
    """Whether any lesson-level or block-level check has status ``fail``."""
    results = [*lesson.checks, *(c for block in lesson.blocks.values() for c in block.checks)]
    return any(check.status is CheckStatus.FAIL for check in results)


def warnings(lesson: Lesson) -> list[tuple[str | None, CheckResult]]:
    """Every ``warn`` result as ``(block_id, result)``: lesson-level first, then blocks in order."""
    found: list[tuple[str | None, CheckResult]] = [
        (None, check) for check in lesson.checks if check.status is CheckStatus.WARN
    ]
    for block in _blocks(lesson):
        found.extend(
            (block.id, check) for check in block.checks if check.status is CheckStatus.WARN
        )
    return found
