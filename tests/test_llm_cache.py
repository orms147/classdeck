from __future__ import annotations

from pathlib import Path

from classdeck.llm import CachingProvider, Message, MockProvider
from classdeck.llm.cache import cache_key

MESSAGES = [Message(role="system", content="be brief"), Message(role="user", content="hi")]


def test_miss_then_hit(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    inner = MockProvider(["first", "second"])
    provider = CachingProvider(inner, cache_dir)
    assert not cache_dir.exists()

    miss = provider.complete(MESSAGES)
    assert (miss.text, miss.cached) == ("first", False)
    assert len(list(cache_dir.glob("*.json"))) == 1

    hit = provider.complete(MESSAGES)
    assert (hit.text, hit.cached, hit.latency_s) == ("first", True, 0.0)
    assert hit.usage == miss.usage
    assert len(inner.calls) == 1


def test_cache_survives_a_new_provider_instance(tmp_path: Path) -> None:
    CachingProvider(MockProvider(["kept"]), tmp_path).complete(MESSAGES)
    inner = MockProvider(["unused"])
    assert CachingProvider(inner, tmp_path).complete(MESSAGES).text == "kept"
    assert inner.calls == []


def test_request_parameters_are_part_of_the_key(tmp_path: Path) -> None:
    inner = MockProvider(["a", "b", "c", "d"])
    provider = CachingProvider(inner, tmp_path)
    provider.complete(MESSAGES, temperature=0.0)
    provider.complete(MESSAGES, temperature=0.5)
    provider.complete(MESSAGES, temperature=0.0, max_tokens=5)
    provider.complete(MESSAGES, temperature=0.0, json_schema={"type": "object"})
    assert len(inner.calls) == 4
    assert len(list(tmp_path.glob("*.json"))) == 4


def test_key_depends_on_provider_identity_and_messages() -> None:
    args = (MESSAGES, 0.2, None, None)
    assert cache_key("a", "m", *args) == cache_key("a", "m", *args)
    assert cache_key("a", "m", *args) != cache_key("b", "m", *args)
    assert cache_key("a", "m", *args) != cache_key("a", "n", *args)
    assert cache_key("a", "m", *args) != cache_key("a", "m", MESSAGES[:1], 0.2, None, None)


def test_name_and_model_come_from_the_inner_provider(tmp_path: Path) -> None:
    provider = CachingProvider(MockProvider(model="mock-9"), tmp_path)
    assert (provider.name, provider.model) == ("mock", "mock-9")


def test_corrupt_file_is_a_miss_and_is_overwritten(tmp_path: Path) -> None:
    inner = MockProvider(["first", "second", "unused"])
    provider = CachingProvider(inner, tmp_path)
    provider.complete(MESSAGES)
    (path,) = tmp_path.glob("*.json")

    path.write_text("{not json", encoding="utf-8")
    assert provider.complete(MESSAGES).text == "second"
    assert len(inner.calls) == 2

    path.write_text("[]", encoding="utf-8")  # valid JSON, wrong shape
    assert provider.complete(MESSAGES).text == "unused"

    hit = provider.complete(MESSAGES)
    assert (hit.text, hit.cached) == ("unused", True)
    assert len(inner.calls) == 3
