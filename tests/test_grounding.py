from __future__ import annotations

import json
from typing import Any

import pytest

from classdeck.grounding import (
    CITATION_SUPPORTED,
    CitationVerdict,
    ai_added_ratio,
    assign_provenance,
    blocks_to_ground,
    comparable_text,
    fill_quotes,
    grounded_ratio,
    grounding_summary,
    judge_confidence,
    provenance_for,
    resolve_citation,
    token_f1,
    verify,
    verify_citation,
    verify_lesson,
)
from classdeck.lesson import (
    Block,
    BlockAdapter,
    BlockKind,
    CheckResult,
    CheckStatus,
    Citation,
    Lesson,
    Provenance,
    Span,
)
from classdeck.llm import LLMError, MockProvider, load_prompt
from tests.test_lesson_schema import DOC_ID, make_lesson, make_source_document

SOURCES = {DOC_ID: make_source_document()}
PASS, WARN, FAIL = CheckStatus.PASS, CheckStatus.WARN, CheckStatus.FAIL
YES = json.dumps({"supported": True, "reason": "The claim restates the source."})
NO = json.dumps({"supported": False, "reason": "The claim adds an action the source lacks."})

# "1. Look, listen and repeat." has five tokens; these activity texts share three of them.
BORDERLINE_HIGH = ("Look and listen", "Point to the picture.")  # F1 = 0.50
BORDERLINE_LOW = ("Look and listen", "Point to the picture and say the word.")  # F1 = 0.375
UNRELATED = ("Name chain", "Pass the ball.")  # F1 = 0.0


# --- helpers -----------------------------------------------------------------------------------


def cite(span_id: str, page: int = 6, **extra: Any) -> dict[str, Any]:
    return {"doc_id": DOC_ID, "page": page, "span_id": span_id, **extra}


def span_by_id(span_id: str) -> Span:
    span = SOURCES[DOC_ID].find_span(6, span_id)
    assert span is not None
    return span


def grounding_checks(block: Block) -> list[CheckResult]:
    return [check for check in block.checks if check.name == CITATION_SUPPORTED]


def the_check(block: Block) -> CheckResult:
    checks = grounding_checks(block)
    assert len(checks) == 1, f"expected one grounding check on {block.id}, got {len(checks)}"
    return checks[0]


def with_activity(
    texts: tuple[str, str],
    span_id: str = "p6.s2",
    *,
    provenance: str = "extracted",
    depends_on: list[str] | None = None,
) -> Lesson:
    """The example lesson with activity ``b_07`` rewritten to cite one span."""
    lesson = make_lesson()
    block: Any = lesson.blocks["b_07"]
    block.content.title, block.content.instructions = texts
    block.sources = [Citation(**cite(span_id))]
    block.provenance = Provenance(provenance)
    block.depends_on = depends_on if depends_on is not None else ["b_03"]
    return lesson


def block_of(kind: str, content: dict[str, Any], **fields: Any) -> Block:
    return BlockAdapter.validate_python({"id": "b_x", "kind": kind, "content": content, **fields})


def leftover() -> CheckResult:
    return CheckResult(name=CITATION_SUPPORTED, status=PASS, detail="left over from an earlier run")


def assert_extracted_blocks_pass(lesson: Lesson) -> None:
    """Invariant 3: every ``extracted`` block carries a passing grounding check."""
    for block in lesson.blocks.values():
        if block.provenance is Provenance.EXTRACTED:
            assert the_check(block).status is PASS, block.id


# --- token_f1 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Hello, I'm Ha.", "Hello, I'm Ha.", 1.0),
        ("hello ha", "HELLO, Ha!", 1.0),
        ("red blue", "green yellow", 0.0),
        ("", "anything", 0.0),
        ("anything", "   ", 0.0),
        ("a b c d", "a b", 2 / 3),
        ("a a b", "a b b", 2 / 3),
        # Pattern slots are not words: they never add to the overlap.
        ("Hello, I'm ___.", "Hello, I'm Ha.", 6 / 7),
        ("___ ___ ___", "Hello, I'm ___.  Hi, ___. I'm ___.", 0.0),
    ],
)
def test_token_f1(a: str, b: str, expected: float) -> None:
    assert token_f1(a, b) == pytest.approx(expected)
    assert token_f1(b, a) == pytest.approx(expected)


def test_borderline_fixture_texts_sit_where_the_tests_assume() -> None:
    source = span_by_id("p6.s2").text
    assert token_f1(" ".join(BORDERLINE_HIGH), source) == pytest.approx(0.5)
    assert token_f1(" ".join(BORDERLINE_LOW), source) == pytest.approx(0.375)
    assert token_f1(" ".join(UNRELATED), source) == 0.0


# --- linker ------------------------------------------------------------------------------------


def test_resolve_citation() -> None:
    span = resolve_citation(Citation(**cite("p6.s3")), SOURCES)
    assert span is not None
    assert span.span_id == "p6.s3"
    assert resolve_citation(Citation(**cite("p6.s99")), SOURCES) is None
    assert resolve_citation(Citation(**cite("p6.s3", page=7)), SOURCES) is None
    assert resolve_citation(Citation(doc_id="d_other", page=6, span_id="p6.s3"), SOURCES) is None


def test_comparable_text_per_kind() -> None:
    lesson = make_lesson()
    assert comparable_text(lesson.blocks["b_02"]) == "hello hi Hello, I'm Ha. Hi, Ha."
    assert comparable_text(lesson.blocks["b_03"]) == "Hello, I'm ___. Hello, I'm Ha."
    assert comparable_text(lesson.blocks["b_07"]) == (
        "Name chain Pass the ball. Say 'Hello, I'm ...' and your name."
    )
    assert comparable_text(lesson.blocks["b_08"]) == "hello, hi Hello, I'm ___."
    assert comparable_text(lesson.blocks["b_01"]) == ""
    dialogue = block_of(
        "dialogue",
        {"lines": [{"speaker": "Ha", "text": "Hello."}, {"speaker": "Tien", "text": "Hi."}]},
    )
    assert comparable_text(dialogue) == "Hello. Hi."
    phonics = block_of(
        "phonics",
        {"sound": "/h/", "letters": "h", "words": ["hello", "hi"], "chant_lines": ["Hi!"]},
    )
    assert comparable_text(phonics) == "/h/ hello hi Hi!"
    assert comparable_text(block_of("image_ref", {"caption": "Ha and Tien", "alt": "x"})) == (
        "Ha and Tien"
    )
    assert comparable_text(block_of("teacher_note", {"text": "Smile."})) == ""


def test_fill_quotes_fills_missing_quotes_to_80_characters() -> None:
    long_text = " ".join(["word"] * 40)
    document = SOURCES[DOC_ID].model_copy(deep=True)
    document.pages[0].spans.append(Span(span_id="p6.s7", kind="paragraph", text=long_text, order=7))
    lesson = make_lesson()
    lesson.blocks["b_03"].sources = [Citation(**cite("p6.s7")), Citation(**cite("p6.s4"))]
    lesson.blocks["b_07"].sources = [Citation(**cite("p6.s99"))]

    filled = fill_quotes(lesson, {DOC_ID: document})

    long_quote, image_quote = (c.quote for c in filled.blocks["b_03"].sources)
    assert long_quote is not None
    assert len(long_quote) == 80
    assert long_text.startswith(long_quote)
    assert image_quote is None  # image spans have no text
    assert filled.blocks["b_07"].sources[0].quote is None  # dangling citations are left alone
    assert filled.blocks["b_02"].sources[0].quote == "Hello, I'm Ha."  # existing quotes kept
    assert lesson.blocks["b_03"].sources[0].quote is None  # input not mutated


# --- verify_citation ---------------------------------------------------------------------------


def test_near_verbatim_text_is_supported_lexically_without_the_judge() -> None:
    provider = MockProvider(responses=[YES])
    text = comparable_text(make_lesson().blocks["b_02"])
    supported, confidence, method, detail = verify_citation(text, span_by_id("p6.s3"), provider)
    assert supported is True
    assert method == "lexical"
    assert confidence == pytest.approx(token_f1(text, span_by_id("p6.s3").text))
    assert confidence >= 0.6
    assert detail.startswith("lexical overlap")
    assert provider.calls == []


def test_unrelated_text_is_rejected_lexically_without_the_judge() -> None:
    provider = MockProvider(responses=[YES])
    outcome = verify_citation(" ".join(UNRELATED), span_by_id("p6.s2"), provider)
    assert (outcome.supported, outcome.confidence, outcome.method) == (False, 0.0, "lexical")
    assert provider.calls == []


@pytest.mark.parametrize(
    ("texts", "reply", "expected", "confidence"),
    [
        (BORDERLINE_HIGH, YES, True, 0.75),  # (1 + 0.5) / 2
        (BORDERLINE_HIGH, NO, False, 0.25),  # 0.5 / 2
        (BORDERLINE_LOW, YES, True, 0.6875),  # (1 + 0.375) / 2
        (BORDERLINE_LOW, NO, False, 0.1875),  # 0.375 / 2
    ],
)
def test_borderline_text_goes_to_the_judge_once(
    texts: tuple[str, str], reply: str, expected: bool, confidence: float
) -> None:
    provider = MockProvider(responses=[reply])
    text = " ".join(texts)
    outcome = verify_citation(text, span_by_id("p6.s2"), provider)
    assert outcome.supported is expected
    assert outcome.confidence == pytest.approx(confidence)
    assert outcome.method == "judge"
    assert "restates" not in outcome.detail  # the judge's free text stays with the judge
    assert "lacks" not in outcome.detail
    assert len(provider.calls) == 1
    sent = provider.calls[0]["messages"]
    assert [message["role"] for message in sent] == ["user"]
    assert text in sent[0]["content"]
    assert span_by_id("p6.s2").text in sent[0]["content"]
    assert "Is the CLAIM based on the SOURCE?" in sent[0]["content"]


def test_judge_confidence_is_monotone_in_the_overlap() -> None:
    accepted = [judge_confidence(overlap, True) for overlap in (0.2, 0.4, 0.59)]
    rejected = [judge_confidence(overlap, False) for overlap in (0.2, 0.4, 0.59)]
    assert accepted == sorted(accepted) and rejected == sorted(rejected)
    assert min(accepted) > max(rejected)


def test_borderline_text_without_a_provider_uses_the_midpoint_rule() -> None:
    high = verify_citation(" ".join(BORDERLINE_HIGH), span_by_id("p6.s2"), None)
    assert (high.supported, high.method) == (True, "midpoint")
    assert high.confidence == pytest.approx(0.5)
    low = verify_citation(" ".join(BORDERLINE_LOW), span_by_id("p6.s2"), None)
    assert (low.supported, low.method) == (False, "midpoint")
    assert low.confidence == pytest.approx(0.375)
    assert "midpoint" in low.detail


def test_thresholds_can_be_overridden() -> None:
    text = " ".join(BORDERLINE_HIGH)
    outcome = verify_citation(text, span_by_id("p6.s2"), None, thresholds=(0.1, 0.4))
    assert (outcome.supported, outcome.method) == (True, "lexical")
    outcome = verify_citation(text, span_by_id("p6.s2"), None, thresholds=(0.55, 0.9))
    assert (outcome.supported, outcome.method) == (False, "lexical")


def test_image_spans_support_only_image_citing_kinds() -> None:
    image = span_by_id("p6.s4")
    for kind in (BlockKind.IMAGE_REF, BlockKind.VOCABULARY):
        outcome = verify_citation("Ha and Tien", image, None, block_kind=kind)
        assert (outcome.supported, outcome.confidence, outcome.method) == (True, 1.0, "image")
    for kind in (BlockKind.ACTIVITY, BlockKind.DIALOGUE, BlockKind.SENTENCE_PATTERN, None):
        outcome = verify_citation("Pass the ball.", image, None, block_kind=kind)
        assert (outcome.supported, outcome.confidence, outcome.method) == (False, 0.0, "image")
        assert outcome.detail == "image span cannot support text content"


def test_a_judge_that_never_answers_valid_json_falls_back_to_the_midpoint_rule() -> None:
    provider = MockProvider(responses=["maybe", "I think so"])
    outcome = verify_citation(" ".join(BORDERLINE_HIGH), span_by_id("p6.s2"), provider)
    assert len(provider.calls) == 2  # one retry, then the fallback
    assert (outcome.supported, outcome.method) == (True, "midpoint")
    assert outcome.confidence == pytest.approx(0.5)
    assert "judge gave no usable answer" in outcome.detail


def test_other_model_failures_still_abort() -> None:
    provider = MockProvider(responses=[])  # raises LLMError on the first call
    with pytest.raises(LLMError):
        verify_citation(" ".join(BORDERLINE_HIGH), span_by_id("p6.s2"), provider)


def test_prompt_has_both_texts_and_the_question() -> None:
    prompt = load_prompt("grounding_judge", block_text="CLAIM-TEXT", span_text="SOURCE-TEXT")
    assert "CLAIM:\nCLAIM-TEXT" in prompt
    assert "SOURCE:\nSOURCE-TEXT" in prompt
    assert '{"supported": true|false, "reason": "..."}' in prompt


# --- verify_lesson -----------------------------------------------------------------------------


def test_example_lesson_vocabulary_is_extracted_and_passes() -> None:
    provider = MockProvider(responses=[YES])
    done = verify_lesson(make_lesson(), SOURCES, provider)
    vocabulary = done.blocks["b_02"]
    assert vocabulary.provenance is Provenance.EXTRACTED
    check = the_check(vocabulary)
    assert check.status is PASS
    assert check.detail == "1/1 supported; lexical overlap 0.71"
    assert vocabulary.sources[0].confidence == pytest.approx(0.706, abs=0.001)
    assert provider.calls == []
    assert_extracted_blocks_pass(done)
    # The other checks on the block survive.
    assert {check.name for check in vocabulary.checks} == {
        CITATION_SUPPORTED,
        "english.ipa_present",
    }


def test_unrelated_block_is_downgraded_to_generated_with_a_warning() -> None:
    done = verify_lesson(with_activity(UNRELATED), SOURCES, None)
    activity = done.blocks["b_07"]
    assert activity.provenance is Provenance.GENERATED
    check = the_check(activity)
    assert check.status is WARN
    assert check.detail == "no supported citation; provenance set to generated"
    assert activity.sources[0].confidence == 0.0


def test_an_activity_citing_an_image_span_is_downgraded_to_generated() -> None:
    done = verify_lesson(with_activity(UNRELATED, span_id="p6.s4"), SOURCES, None)
    activity = done.blocks["b_07"]
    assert activity.provenance is Provenance.GENERATED
    check = the_check(activity)
    assert check.status is WARN
    assert check.detail == "no supported citation; provenance set to generated"
    assert activity.sources[0].confidence == 0.0


def test_an_image_ref_citing_an_image_span_is_extracted() -> None:
    lesson = make_lesson()
    lesson.blocks["b_04"] = block_of(
        "image_ref", {"caption": "Ha and Tien", "alt": "Two pupils greeting"}, id="b_04"
    )
    lesson.blocks["b_04"].sources = [Citation(**cite("p6.s4"))]
    lesson.sections[0].block_ids.append("b_04")
    done = verify_lesson(lesson, SOURCES, None)
    image_ref = done.blocks["b_04"]
    assert image_ref.provenance is Provenance.EXTRACTED
    check = the_check(image_ref)
    assert check.status is PASS
    assert check.detail == "1/1 supported; image span resolved; the picture is reused"
    assert image_ref.sources[0].confidence == 1.0
    assert image_ref.sources[0].quote is None
    assert_extracted_blocks_pass(done)


@pytest.mark.parametrize(
    ("reply", "provenance", "status"),
    [(YES, Provenance.EXTRACTED, PASS), (NO, Provenance.GENERATED, WARN)],
)
def test_borderline_block_follows_the_judge(
    reply: str, provenance: Provenance, status: CheckStatus
) -> None:
    provider = MockProvider(responses=[reply])
    done = verify_lesson(with_activity(BORDERLINE_HIGH), SOURCES, provider)
    assert len(provider.calls) == 1
    activity = done.blocks["b_07"]
    assert activity.provenance is provenance
    check = the_check(activity)
    assert check.status is status
    if status is PASS:
        assert check.detail == "1/1 supported; judge: supported (lexical overlap 0.50)"
        assert activity.sources[0].confidence == pytest.approx(judge_confidence(0.5, True))
    else:
        assert activity.sources[0].confidence == pytest.approx(judge_confidence(0.5, False))
    # Model free text never reaches the lesson.
    assert "restates" not in (check.detail or "")
    assert "lacks" not in (check.detail or "")
    assert_extracted_blocks_pass(done)


def test_borderline_block_without_a_provider_uses_the_midpoint_rule() -> None:
    done = verify_lesson(with_activity(BORDERLINE_HIGH), SOURCES, None)
    assert done.blocks["b_07"].provenance is Provenance.EXTRACTED
    assert the_check(done.blocks["b_07"]).detail == (
        "1/1 supported; lexical overlap 0.50; midpoint rule at 0.40 (no judge available)"
    )
    assert done.blocks["b_07"].sources[0].confidence == pytest.approx(0.5)

    done = verify_lesson(with_activity(BORDERLINE_LOW), SOURCES, None)
    assert done.blocks["b_07"].provenance is Provenance.GENERATED
    assert the_check(done.blocks["b_07"]).status is WARN


def test_verify_lesson_completes_with_the_bare_mock_provider() -> None:
    """``CLASSDECK_PROVIDER=mock`` answers "mock response"; the stage must still finish."""
    provider = MockProvider()
    done = verify_lesson(with_activity(BORDERLINE_HIGH), SOURCES, provider)
    assert len(provider.calls) == 2
    activity = done.blocks["b_07"]
    assert activity.provenance is Provenance.EXTRACTED
    check = the_check(activity)
    assert check.status is PASS
    assert "midpoint rule at 0.40 (judge gave no usable answer)" in (check.detail or "")
    assert_extracted_blocks_pass(done)


def test_dangling_citation_fails_the_check() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources = [Citation(**cite("p6.s99"))]
    done = verify_lesson(lesson, SOURCES, None)
    pattern = done.blocks["b_03"]
    check = the_check(pattern)
    assert check.status is FAIL
    assert check.detail == f"1/1 citation(s) dangling: p6.s99 (page 6, {DOC_ID})"
    assert pattern.sources[0].confidence == 0.0
    assert pattern.provenance is Provenance.GENERATED


def test_a_dangling_citation_next_to_a_supported_one_passes_and_is_named() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources.append(Citation(**cite("p6.s99")))
    done = verify_lesson(lesson, SOURCES, None)
    pattern = done.blocks["b_03"]
    check = the_check(pattern)
    assert check.status is PASS
    assert check.detail == (
        f"1/2 supported; lexical overlap 0.77; dangling: p6.s99 (page 6, {DOC_ID})"
    )
    assert pattern.provenance is Provenance.EXTRACTED
    assert [c.confidence for c in pattern.sources] == [pytest.approx(0.769, abs=0.001), 0.0]
    assert grounding_summary(done)["dangling"] == 0  # the block is grounded, so not a failure
    assert_extracted_blocks_pass(done)


def test_derived_activity_with_dependencies_keeps_derived() -> None:
    lesson = with_activity(UNRELATED, provenance="derived", depends_on=["b_02", "b_03"])
    done = verify_lesson(lesson, SOURCES, None)
    activity = done.blocks["b_07"]
    assert activity.provenance is Provenance.DERIVED
    check = the_check(activity)
    assert check.status is WARN
    assert check.detail == "no supported citation; provenance kept derived"


def test_derived_activity_without_dependencies_becomes_generated() -> None:
    lesson = with_activity(UNRELATED, provenance="derived", depends_on=[])
    done = verify_lesson(lesson, SOURCES, None)
    assert done.blocks["b_07"].provenance is Provenance.GENERATED


def test_framing_blocks_are_untouched_and_lose_leftover_grounding_checks() -> None:
    lesson = make_lesson()
    lesson.blocks["b_08"].sources = [Citation(**cite("p6.s99"))]  # even a dangling one
    lesson.blocks["b_08"].checks.append(leftover())
    lesson.blocks["b_01"].checks.append(leftover())
    done = verify_lesson(lesson, SOURCES, None)
    objective, summary = done.blocks["b_01"], done.blocks["b_08"]
    assert objective.provenance is Provenance.DERIVED
    assert objective.checks == []
    assert summary.provenance is Provenance.DERIVED
    assert summary.depends_on == ["b_02", "b_07"]
    assert summary.checks == []
    assert summary.sources[0].confidence is None


def test_uncited_content_blocks_get_no_check_and_a_stale_one_is_removed() -> None:
    lesson = make_lesson()
    lesson.blocks["b_07"].checks.append(leftover())
    done = verify_lesson(lesson, SOURCES, None)
    activity = done.blocks["b_07"]
    assert grounding_checks(activity) == []
    assert [check.name for check in activity.checks] == ["english.pattern_used"]
    assert activity.provenance is Provenance.GENERATED


def test_blocks_outside_every_section_are_verified_and_counted() -> None:
    lesson = make_lesson()
    lesson.blocks["b_09"] = block_of(
        "dialogue",
        {"lines": [{"speaker": "Ha", "text": "Hello, I'm Ha."}]},
        id="b_09",
        sources=[cite("p6.s99")],
        provenance="extracted",
        checks=[leftover().model_dump(mode="json")],
    )
    lesson.blocks["b_10"] = block_of(
        "dialogue",
        {
            "lines": [
                {"speaker": "Ha", "text": "Hello, I'm Ha."},
                {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
            ]
        },
        id="b_10",
        sources=[cite("p6.s3")],
        provenance="generated",
    )
    assert [block.id for block in lesson.content_blocks()] == ["b_02", "b_03", "b_07"]
    assert [block.id for block in blocks_to_ground(lesson)] == [
        "b_02",
        "b_03",
        "b_07",
        "b_09",
        "b_10",
    ]

    done = verify_lesson(lesson, SOURCES, None)

    dangling = done.blocks["b_09"]
    assert the_check(dangling).status is FAIL
    assert dangling.provenance is Provenance.GENERATED
    assert dangling.sources[0].confidence == 0.0
    verbatim = done.blocks["b_10"]
    assert the_check(verbatim).status is PASS
    assert verbatim.provenance is Provenance.EXTRACTED
    assert verbatim.sources[0].confidence == pytest.approx(1.0)
    assert grounding_summary(done) == {
        "content_blocks": 5,
        "grounded": 3,
        "generated": 2,
        "dangling": 1,
    }
    assert grounded_ratio(done) == pytest.approx(3 / 5)
    assert ai_added_ratio(done) == pytest.approx(2 / 5)
    assert_extracted_blocks_pass(done)


def test_verify_twice_gives_one_check_per_block_and_leaves_the_input_alone() -> None:
    lesson = make_lesson()
    lesson.blocks["b_02"].sources[0].quote = None
    snapshot = lesson.model_dump(mode="json")

    once = verify_lesson(lesson, SOURCES, None)
    twice = verify_lesson(once, SOURCES, None)

    assert lesson.model_dump(mode="json") == snapshot
    for block_id in ("b_02", "b_03"):
        assert len(grounding_checks(once.blocks[block_id])) == 1
        assert len(grounding_checks(twice.blocks[block_id])) == 1
        first, second = the_check(once.blocks[block_id]), the_check(twice.blocks[block_id])
        assert (first.status, first.detail) == (second.status, second.detail)
    strip = {"updated_at", "created_at"}
    dump_once = {k: v for k, v in once.model_dump(mode="json").items() if k not in strip}
    dump_twice = {k: v for k, v in twice.model_dump(mode="json").items() if k not in strip}
    for dump in (dump_once, dump_twice):
        for block in dump["blocks"].values():
            for check in block["checks"]:
                check.pop("at")
    assert dump_once == dump_twice


def test_verify_fills_missing_quotes() -> None:
    lesson = make_lesson()
    lesson.blocks["b_02"].sources[0].quote = None
    done = verify_lesson(lesson, SOURCES, None)
    assert done.blocks["b_02"].sources[0].quote == "Hello, I'm Ha. Hi, Ha. I'm Tien."
    assert done.blocks["b_03"].sources[0].quote == "Hello, I'm ___. Hi, ___. I'm ___."


def test_verify_is_the_documented_alias() -> None:
    assert verify is verify_lesson


# --- provenance --------------------------------------------------------------------------------


def test_provenance_for() -> None:
    lesson = make_lesson()
    objective, vocabulary, activity, summary = (
        lesson.blocks[b] for b in ("b_01", "b_02", "b_07", "b_08")
    )
    assert provenance_for(objective, supported=False) is Provenance.DERIVED
    assert provenance_for(summary, supported=True) is Provenance.DERIVED
    assert provenance_for(vocabulary, supported=True) is Provenance.EXTRACTED
    assert provenance_for(vocabulary, supported=False) is Provenance.GENERATED
    assert provenance_for(activity, supported=True) is Provenance.EXTRACTED
    activity.provenance = Provenance.DERIVED
    assert provenance_for(activity, supported=False) is Provenance.DERIVED
    activity.depends_on = []
    assert provenance_for(activity, supported=False) is Provenance.GENERATED


def test_assign_provenance_reads_the_verdicts() -> None:
    lesson = make_lesson()
    lesson.blocks["b_09"] = block_of(  # outside every section, no verdict
        "image_ref", {"caption": "", "alt": "Two pupils"}, id="b_09", provenance="extracted"
    )
    verdict = CitationVerdict(
        block_id="b_03",
        citation=lesson.blocks["b_03"].sources[0],
        supported=True,
        confidence=0.9,
        method="lexical",
        detail="lexical overlap 0.90",
    )
    done = assign_provenance(lesson, [verdict])
    assert done.blocks["b_03"].provenance is Provenance.EXTRACTED
    assert done.blocks["b_02"].provenance is Provenance.GENERATED  # no verdict for it
    assert done.blocks["b_07"].provenance is Provenance.GENERATED
    assert done.blocks["b_09"].provenance is Provenance.GENERATED
    assert done.blocks["b_01"].provenance is Provenance.DERIVED
    assert done.blocks["b_08"].provenance is Provenance.DERIVED
    assert lesson.blocks["b_02"].provenance is Provenance.EXTRACTED  # input not mutated


# --- metrics -----------------------------------------------------------------------------------


def test_ratios_on_the_example_lesson() -> None:
    before = make_lesson()
    assert grounded_ratio(before) == pytest.approx(1 / 3)  # only b_02 carries a pass already
    assert ai_added_ratio(before) == pytest.approx(1 / 3)

    done = verify_lesson(before, SOURCES, None)
    assert grounded_ratio(done) == pytest.approx(2 / 3)
    assert ai_added_ratio(done) == pytest.approx(1 / 3)
    assert grounding_summary(done) == {
        "content_blocks": 3,
        "grounded": 2,
        "generated": 1,
        "dangling": 0,
    }


def test_ratios_count_dangling_and_handle_empty_lessons() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources = [Citation(**cite("p6.s99"))]
    done = verify_lesson(lesson, SOURCES, None)
    assert grounding_summary(done) == {
        "content_blocks": 3,
        "grounded": 1,
        "generated": 2,
        "dangling": 1,
    }
    assert grounded_ratio(done) == pytest.approx(1 / 3)

    empty = make_lesson()
    for section in empty.sections:
        section.block_ids = [b for b in section.block_ids if b in ("b_01", "b_08")]
    # Unplaced content blocks still count (they are verified), so remove them outright.
    for block_id in ("b_02", "b_03", "b_07"):
        del empty.blocks[block_id]
    empty.blocks["b_08"].depends_on = []
    assert empty.content_blocks() == []
    assert blocks_to_ground(empty) == []
    assert grounded_ratio(empty) == 0.0
    assert ai_added_ratio(empty) == 0.0
    assert grounding_summary(empty)["content_blocks"] == 0


# --- copied steps and uncitable derived blocks -------------------------------------------------


def test_an_activity_is_supported_by_the_steps_that_copy_its_span() -> None:
    quote = span_by_id("p6.s3").text
    lesson = with_activity(UNRELATED, "p6.s3")
    plain = verify_lesson(lesson, SOURCES, None)
    assert the_check(plain.blocks["b_07"]).status is WARN  # the title and instructions say nothing

    block: Any = lesson.blocks["b_07"]
    block.content.student_steps = [quote, "Check your answers with a friend."]
    copied = verify_lesson(lesson, SOURCES, None)
    assert the_check(copied.blocks["b_07"]).status is PASS
    assert copied.blocks["b_07"].sources[0].confidence is not None


def test_derived_blocks_without_citations_are_not_counted() -> None:
    lesson = make_lesson()
    base = len(blocks_to_ground(lesson))
    block: Any = lesson.blocks["b_07"]
    block.sources = []
    block.provenance = Provenance.DERIVED
    summary = grounding_summary(lesson)
    assert summary["content_blocks"] == base - 1
    block.provenance = Provenance.GENERATED
    assert grounding_summary(lesson)["content_blocks"] == base
