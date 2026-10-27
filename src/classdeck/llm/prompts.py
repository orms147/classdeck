"""Prompt templates stored as Markdown files in ``llm/prompts/``."""

from __future__ import annotations

import re
from importlib.resources import files

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_prompt(name: str, /, **variables: str) -> str:
    """Read ``prompts/<name>.md`` and replace each ``{{var}}`` with ``variables["var"]``.

    Raises ``FileNotFoundError`` for an unknown prompt and ``KeyError`` naming any placeholder
    that has no value. ``name`` is positional-only so templates may use a ``{{name}}``
    placeholder. Substitution is single-pass, so a value that itself contains ``{{x}}``
    is left as written.
    """
    resource = files("classdeck.llm") / "prompts" / f"{name}.md"
    try:
        template = resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"No prompt named {name!r} in classdeck/llm/prompts") from exc

    missing = sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)} - variables.keys())
    if missing:
        raise KeyError(f"Prompt {name!r} has unfilled placeholders: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda m: variables[m.group(1)], template)
