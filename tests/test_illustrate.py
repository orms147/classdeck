from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from classdeck.illustrate import (
    CONTENT_POS,
    STYLE_GUIDE,
    build_prompt,
    candidates,
    downscale_png,
    illustrate,
    picture_path,
)
from classdeck.lesson import Lesson, VocabItem, VocabularyBlock
from classdeck.llm.images import MockImageProvider
from classdeck.llm.pricing import estimate_image_cost_usd
from tests.test_export import make_export_lesson

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_lesson() -> Lesson:
    """b_02 holds 'hello' (cited picture) and 'hi'; a second block adds words of every kind."""
    lesson = make_export_lesson()
    block = lesson.blocks["b_02"]
    assert isinstance(block, VocabularyBlock)
    block.content.items = [
        block.content.items[0],
        VocabItem(word="hi", pos="interjection", meaning_vi="chào", example="Hi, Tien!"),
        VocabItem(word="cat", pos="noun", meaning_vi="con mèo", example="A cat."),
        VocabItem(word="he", pos="pronoun"),
        VocabItem(word="run"),
        VocabItem(word="in", pos="preposition"),
    ]
    return lesson


@pytest.fixture
def lesson() -> Lesson:
    return make_lesson()


def words(found: list[tuple[str, int, VocabItem]]) -> list[str]:
    return [item.word for _, _, item in found]


def test_candidates_skip_cited_pictures_and_function_words(lesson: Lesson) -> None:
    found = candidates(lesson)
    assert words(found) == ["hi", "cat", "run"]  # a missing pos counts as content
    assert [(block_id, index) for block_id, index, _ in found] == [
        ("b_02", 1),
        ("b_02", 2),
        ("b_02", 4),
    ]
    assert {"noun", "verb", "adjective", "interjection", "adverb"} == CONTENT_POS


def test_candidates_all_items_includes_function_words(lesson: Lesson) -> None:
    assert words(candidates(lesson, all_items=True)) == ["hi", "cat", "he", "run", "in"]


def test_candidates_skip_items_that_already_have_a_generated_picture(lesson: Lesson) -> None:
    block = lesson.blocks["b_02"]
    assert isinstance(block, VocabularyBlock)
    block.content.items[1].generated_picture = "generated/abc.png"
    assert words(candidates(lesson)) == ["cat", "run"]


def test_prompt_has_style_guide_word_gloss_and_example(lesson: Lesson) -> None:
    item = VocabItem(word="cat", pos="noun", meaning_vi="con mèo", example="A cat.")
    prompt = build_prompt(item, lesson)
    assert STYLE_GUIDE in prompt
    assert "word: cat" in prompt
    assert "con mèo" in prompt
    assert "A cat." in prompt
    assert "no text" in STYLE_GUIDE.lower()


def test_picture_path_is_stable_and_prompt_specific() -> None:
    first = picture_path("prompt one")
    assert first == picture_path("prompt one")
    assert first != picture_path("prompt two")
    folder, name = first.split("/")
    assert folder == "generated"
    assert name.endswith(".png") and len(name) == 16 + len(".png")


def big_png(width: int, height: int) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(180)
    return pixmap.tobytes("png")


def test_downscale_keeps_png_and_caps_the_long_side() -> None:
    small = downscale_png(big_png(1024, 768))
    assert small.startswith(PNG_MAGIC)
    pixmap = pymupdf.Pixmap(small)
    assert (pixmap.width, pixmap.height) == (512, 384)
    assert len(small) < len(big_png(1024, 768))


def test_downscale_leaves_small_pictures_at_their_size() -> None:
    pixmap = pymupdf.Pixmap(downscale_png(big_png(200, 300)))
    assert (pixmap.width, pixmap.height) == (200, 300)


def test_mock_provider_draws_a_png_and_records_calls(lesson: Lesson) -> None:
    provider = MockImageProvider()
    prompt = build_prompt(VocabItem(word="cat"), lesson)
    result = provider.generate(prompt)
    assert result.png.startswith(PNG_MAGIC)
    assert result.model == "mock-image"
    assert provider.calls == [prompt]
    pixmap = pymupdf.Pixmap(result.png)
    assert (pixmap.width, pixmap.height) == (512, 512)


def test_illustrate_writes_files_and_sets_generated_picture(lesson: Lesson, tmp_path: Path) -> None:
    provider = MockImageProvider()
    report = illustrate(lesson, provider=provider, image_root=tmp_path)
    assert (report.generated, report.reused, report.skipped) == (3, 0, 0)
    assert len(provider.calls) == 3
    assert report.words == ["hi", "cat", "run"]
    block = report.lesson.blocks["b_02"]
    assert isinstance(block, VocabularyBlock)
    items = block.content.items
    for index in (1, 2, 4):
        relative = items[index].generated_picture
        assert relative is not None and relative.startswith("generated/")
        data = (tmp_path / relative).read_bytes()
        assert data.startswith(PNG_MAGIC)
    assert items[0].generated_picture is None and items[3].generated_picture is None
    assert report.cost_usd is None  # the mock model has no price
    # The lesson stays valid when saved and loaded.
    assert Lesson.model_validate_json(report.lesson.model_dump_json()) == report.lesson


def test_second_run_reuses_files_without_calling_the_provider(
    lesson: Lesson, tmp_path: Path
) -> None:
    first = illustrate(lesson, provider=MockImageProvider(), image_root=tmp_path)
    provider = MockImageProvider()
    again = illustrate(lesson, provider=provider, image_root=tmp_path)
    assert provider.calls == []
    assert (again.generated, again.reused) == (0, 3)
    assert again.lesson == first.lesson


def test_max_limits_new_pictures_and_counts_the_rest_as_skipped(
    lesson: Lesson, tmp_path: Path
) -> None:
    provider = MockImageProvider()
    report = illustrate(lesson, provider=provider, image_root=tmp_path, max_items=2)
    assert (report.generated, report.skipped) == (2, 1)
    assert len(provider.calls) == 2
    assert len(list((tmp_path / "generated").glob("*.png"))) == 2
    # The next run draws the remaining word and reuses the other two.
    nxt = illustrate(lesson, provider=provider, image_root=tmp_path, max_items=2)
    assert (nxt.generated, nxt.reused, nxt.skipped) == (1, 2, 0)


def test_dry_run_writes_nothing_and_prices_the_plan(lesson: Lesson, tmp_path: Path) -> None:
    provider = MockImageProvider()
    provider.model = "muse-image-1.0"
    report = illustrate(lesson, provider=provider, image_root=tmp_path, dry_run=True)
    assert provider.calls == []
    assert not (tmp_path / "generated").exists()
    assert (report.generated, report.planned) == (0, 3)
    assert len(report.prompts) == 3
    assert report.cost_usd == pytest.approx(0.03)
    assert report.lesson == lesson


def test_input_lesson_is_not_mutated(lesson: Lesson, tmp_path: Path) -> None:
    before = lesson.model_dump_json()
    report = illustrate(lesson, provider=MockImageProvider(), image_root=tmp_path)
    assert lesson.model_dump_json() == before
    assert report.lesson is not lesson


def test_all_items_draws_function_words_too(lesson: Lesson, tmp_path: Path) -> None:
    report = illustrate(lesson, provider=MockImageProvider(), image_root=tmp_path, all_items=True)
    assert report.generated == 5


def test_image_price_table() -> None:
    assert estimate_image_cost_usd("muse-image-1.0", 4) == pytest.approx(0.04)
    assert estimate_image_cost_usd("unknown-model", 4) is None


class FakeImages:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.kwargs: dict[str, object] = {}

    def generate(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: object | Exception) -> None:
        self.images = FakeImages(response)


def test_openai_compatible_provider_decodes_b64_png() -> None:
    import base64
    from types import SimpleNamespace

    from classdeck.llm.images import OpenAICompatibleImageProvider

    png = big_png(16, 16)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png).decode(), url=None)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=1290),
    )
    client = FakeClient(response)
    provider = OpenAICompatibleImageProvider(
        "api:test", api_key="k", base_url="http://x", model="muse-image-1.0", client=client
    )
    result = provider.generate("draw a cat")
    assert result.png == png
    assert result.usage is not None and result.usage.output_tokens == 1290
    assert client.images.kwargs == {
        "model": "muse-image-1.0",
        "prompt": "draw a cat",
        "n": 1,
        "output_format": "png",
    }


def test_openai_compatible_provider_maps_errors_and_missing_data() -> None:
    from types import SimpleNamespace

    import openai

    try:  # openai 3.x ships its own copy of httpx
        import httpx2 as httpx
    except ImportError:
        import httpx

    from classdeck.llm.base import LLMError
    from classdeck.llm.images import OpenAICompatibleImageProvider

    request = httpx.Request("POST", "http://x/images/generations")
    error = openai.APIStatusError(
        "boom", response=httpx.Response(500, request=request, text="oops"), body=None
    )
    failing = OpenAICompatibleImageProvider(
        "api:test", api_key="k", base_url="http://x", model="m", client=FakeClient(error)
    )
    with pytest.raises(LLMError) as caught:
        failing.generate("x")
    assert caught.value.status == 500

    empty = OpenAICompatibleImageProvider(
        "api:test",
        api_key="k",
        base_url="http://x",
        model="m",
        client=FakeClient(SimpleNamespace(data=[SimpleNamespace(b64_json=None)], usage=None)),
    )
    with pytest.raises(LLMError, match="no image data"):
        empty.generate("x")


def test_build_image_provider_follows_the_settings() -> None:
    from classdeck.config import Settings
    from classdeck.llm.base import LLMError
    from classdeck.llm.images import OpenAICompatibleImageProvider, build_image_provider

    assert isinstance(build_image_provider(Settings(provider="mock")), MockImageProvider)
    keyed = Settings(
        provider="api", model_api_key="secret-key", image_model="muse-image-1.0"
    ).resolved()
    provider = build_image_provider(keyed)
    assert isinstance(provider, OpenAICompatibleImageProvider)
    assert provider.model == "muse-image-1.0"
    with pytest.raises(LLMError):
        build_image_provider(Settings(provider="api", model_api_key=None))


def test_image_model_setting_is_shown_and_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    from classdeck.config import Settings

    assert Settings(_env_file=None).redacted()["CLASSDECK_IMAGE_MODEL"] == "muse-image-1.0"
    monkeypatch.setenv("CLASSDECK_IMAGE_MODEL", "other-image-model")
    assert Settings(_env_file=None).image_model == "other-image-model"
