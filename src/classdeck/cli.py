"""Command-line interface: the Phase 1 pipeline end to end.

``ingest -> plan -> draft -> check -> edit/undo/redo -> export``, plus ``handoff`` (prompt packs
for a free chat model) and ``eval``. Every command works offline with
``CLASSDECK_PROVIDER=mock`` (template pipeline); a real endpoint is used when
``CLASSDECK_PROVIDER=api`` and ``MODEL_API_KEY`` is set.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from classdeck import __version__
from classdeck.config import Settings, get_settings


def _utf8_console() -> None:
    """Make stdout/stderr UTF-8 so Vietnamese glosses and IPA print on legacy Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):  # closed or non-reconfigurable stream
            reconfigure(encoding="utf-8", errors="replace")


_utf8_console()

app = typer.Typer(
    name="classdeck",
    help="Eye-catching, source-grounded lessons for primary-school English.",
    no_args_is_help=True,
    add_completion=False,
)
handoff_app = typer.Typer(help="Prompt packs for a free chat model, and checking what comes back.")
app.add_typer(handoff_app, name="handoff")
console = Console()

CURRICULUM_VERSION = "gdpt-2018"


# --------------------------------------------------------------------------- helpers


def _version(value: bool) -> None:
    if value:
        console.print(f"classdeck {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", callback=_version, is_eager=True
    ),
) -> None:
    """ClassDeck command-line interface."""


def _settings(
    provider: str | None = None, endpoint: str | None = None, model_id: str | None = None
) -> Settings:
    """Effective settings for one command: provider override, then the endpoint profile."""
    settings = get_settings()
    if provider:
        settings = settings.model_copy(update={"provider": provider})
    try:
        return settings.resolved(endpoint, model_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc


ENDPOINT_HELP = "Named endpoint profile: meta | openrouter | deepseek (overrides MODEL_API_*)."
MODEL_HELP = "Model id on the endpoint (default: the profile's or CLASSDECK_MODEL)."


def _generator(settings: Settings, *, use_cache: bool = True) -> Any:
    """The model provider for the pipeline, or ``None`` for the offline template pipeline."""
    if settings.provider == "mock":
        return None
    from classdeck.llm import build_provider

    return build_provider(settings, use_cache=use_cache)


def _curriculum(
    settings: Settings,
    grade: int,
    version: str = CURRICULUM_VERSION,
    *,
    ref: Any = None,
    sources: Mapping[str, Any] | None = None,
) -> Any:
    """The curriculum of a grade. With ``ref`` (a lesson's or outline's curriculum reference) and
    ``sources`` the unit may be one built from the pages (`curriculum/auto.py`)."""
    from classdeck.curriculum.auto import curriculum_for, parse_unit_number

    if ref is not None and sources is not None:
        return curriculum_for(
            grade,
            parse_unit_number(ref.unit_id, grade),
            sources,
            curriculum_dir=settings.curriculum_dir,
            version=version,
        )
    from classdeck.curriculum import load_curriculum

    return load_curriculum(settings.curriculum_dir, version, grade)


def _unit_id(grade: int, unit: str) -> str:
    """The internal unit id of ``--unit``: a number (``2``) or an id (``ta3.u2``)."""
    from classdeck.curriculum.auto import parse_unit_number, unit_id_for

    try:
        return unit_id_for(grade, parse_unit_number(unit, grade))
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc


def _curriculum_for(
    settings: Settings, grade: int, unit_id: str, sources: Mapping[str, Any]
) -> Any:
    from classdeck.curriculum.auto import curriculum_for

    return curriculum_for(
        grade, unit_id, sources, curriculum_dir=settings.curriculum_dir, version=CURRICULUM_VERSION
    )


def _stores(settings: Settings) -> tuple[Any, Any]:
    from classdeck.lesson import LessonStore, SourceStore

    return LessonStore(settings.workdir), SourceStore(settings.workdir)


def _image_root(settings: Settings) -> Path:
    return settings.workdir / "images"


def _load_lesson(settings: Settings, ref: str) -> tuple[Any, Path]:
    """A lesson by id (from the store) or by path. Returns the lesson and where to save it."""
    from classdeck.lesson import Lesson

    path = Path(ref)
    if path.is_file():
        return Lesson.model_validate_json(path.read_text(encoding="utf-8")), path
    store, _ = _stores(settings)
    return store.load(ref), store.path_for(ref)


def _save_lesson(lesson: Any, path: Path) -> Path:
    lesson.touch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(lesson.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _load_sources(settings: Settings, doc_ids: Iterable[str]) -> dict[str, Any]:
    _, sources = _stores(settings)
    out: dict[str, Any] = {}
    for doc_id in doc_ids:
        try:
            out[doc_id] = sources.load(doc_id)
        except FileNotFoundError:
            console.print(f"[yellow]source {doc_id} not found in {settings.workdir}/sources[/]")
    return out


def _load_outline(path: Path) -> Any:
    from classdeck.planning import Outline

    return Outline.model_validate_json(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _print_blocks(lesson: Any) -> None:
    table = Table(show_header=True, header_style="bold", title=lesson.title)
    for column in ("phase", "id", "kind", "provenance", "cites", "checks"):
        table.add_column(column)
    for block in lesson.ordered_blocks():
        section = lesson.section_of(block.id)
        checks = ", ".join(f"{c.name.split('.')[-1]}={c.status}" for c in block.checks)
        table.add_row(
            section.phase if section else "-",
            block.id,
            block.kind,
            block.provenance,
            str(len(block.sources)),
            checks,
        )
    console.print(table)
    for check in lesson.checks:
        console.print(f"  lesson: {check.name} = {check.status}  {check.detail or ''}")


def _print_warnings(lesson: Any) -> None:
    from classdeck.english.validators import has_failures, warnings

    for block_id, check in warnings(lesson):
        where = block_id or "lesson"
        console.print(f"  [yellow]warn[/] {where} {check.name}: {check.detail or ''}")
    if has_failures(lesson):
        console.print(
            "[red]The lesson has failing checks; export is blocked until they are fixed.[/]"
        )


def _finish_lesson(lesson: Any, sources: Mapping[str, Any], curriculum: Any, judge: Any) -> Any:
    """Dependency inference, grounding, level checks: the re-check every saved lesson can run."""
    from classdeck.pipeline import finish_lesson

    return finish_lesson(lesson, sources, curriculum, judge)


def _diff(before: Any, after: Any, title: str) -> None:
    a = json.dumps(before.model_dump(mode="json"), indent=2, ensure_ascii=False).splitlines()
    b = json.dumps(after.model_dump(mode="json"), indent=2, ensure_ascii=False).splitlines()
    console.print(f"[bold]{title}[/]")
    for line in difflib.unified_diff(a, b, "before", "after", lineterm="", n=2):
        colour = "green" if line.startswith("+") else "red" if line.startswith("-") else None
        console.print(f"[{colour}]{line}[/]" if colour else line, highlight=False)


# --------------------------------------------------------------------------- basics


@app.command()
def config() -> None:
    """Show the effective settings (API keys masked)."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("setting")
    table.add_column("value")
    for key, value in get_settings().redacted().items():
        table.add_row(key, value)
    console.print(table)


@app.command()
def ping(
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the response cache."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
) -> None:
    """Send one tiny request to the configured endpoint to confirm the key works."""
    from classdeck.llm.base import LLMError, Message
    from classdeck.llm.factory import build_provider

    try:
        provider = build_provider(_settings(None, endpoint, model_id), use_cache=not no_cache)
        response = provider.complete(
            [Message(role="user", content="Reply with the single word: pong")],
            temperature=0.0,
            # Reasoning-style models spend part of the budget before answering; keep room.
            max_tokens=64,
        )
    except LLMError as exc:
        console.print(f"[red]ping failed:[/] {exc}")
        raise typer.Exit(1) from exc

    usage = response.usage
    text = response.text.strip() or "(empty reply: the model spent its budget on reasoning)"
    # Plain ASCII punctuation: Windows consoles on legacy code pages reject arrows.
    console.print(
        f"[green]{response.model}[/] -> {text!r}  "
        f"(in={usage.input_tokens}, out={usage.output_tokens}, cached={response.cached})"
    )


# --------------------------------------------------------------------------- pipeline


def _page_numbers(spec: str | None) -> set[int] | None:
    """Parse ``1-3,7`` into page numbers; ``None`` for no restriction."""
    if not spec:
        return None
    pages: set[int] = set()
    try:
        for part in spec.split(","):
            first, _, last = part.strip().partition("-")
            pages.update(range(int(first), int(last or first) + 1))
    except ValueError as exc:
        console.print(f"[red]--pages expects numbers like 1-3,7 (got {spec!r})[/]")
        raise typer.Exit(2) from exc
    return pages


def _scan_provider(settings: Settings) -> Any:
    """The vision model for scanned pages, or ``None`` (offline, or no key: pages stay pictures)."""
    from classdeck.llm.base import LLMError

    try:
        return _generator(settings)
    except LLMError:
        return None


def _print_scan_reports(reports: list[Any]) -> None:
    for item in reports:
        cost = "n/a" if item.cost_usd is None else f"${item.cost_usd:.4f}"
        state = "cached" if item.cached else ("ok" if item.ok else f"failed: {item.error}")
        console.print(
            f"  scan p{item.page}: {item.model or '-'}  in={item.input_tokens} "
            f"out={item.output_tokens}  {cost}  ({state})"
        )
    unread = [item.page for item in reports if not item.ok]
    if unread:
        console.print(
            f"[bold red]  {len(unread)} scanned page(s) were NOT read "
            f"(p{', p'.join(map(str, unread))}) and are kept as pictures: "
            "the lesson will miss their text. Check the key and connection and run again.[/]"
        )
    if reports:
        known = [item.cost_usd for item in reports if item.cost_usd is not None]
        console.print(
            f"  scanned pages read: {len(reports)}, "
            f"{sum(item.input_tokens for item in reports)} in / "
            f"{sum(item.output_tokens for item in reports)} out tokens, "
            f"${sum(known):.4f}"
        )


@app.command()
def ingest(
    file: Path = typer.Argument(..., exists=True, readable=True, help="PDF, DOCX or PPTX."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
    pages: str | None = typer.Option(
        None, "--pages", help="PDF pages to keep, for example 1-2 or 1,3-4 (default: all)."
    ),
) -> None:
    """Extract a document into pages and spans; pictures go to <workdir>/images.

    Scanned PDF pages (a picture and no text) are read by the model endpoint when one is set.
    """
    from classdeck.ingest import UnsupportedFormat
    from classdeck.ingest import ingest as ingest_file

    settings = _settings(None, endpoint, model_id)
    reports: list[Any] = []
    try:
        doc = ingest_file(
            file,
            image_dir=_image_root(settings),
            provider=_scan_provider(settings),
            pages=_page_numbers(pages),
            report=reports.append,
        )
    except UnsupportedFormat as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    _, sources = _stores(settings)
    path = sources.save(doc)
    spans = sum(len(page.spans) for page in doc.pages)
    images = sum(1 for page in doc.pages for span in page.spans if span.kind == "image")
    console.print(
        f"[green]{doc.doc_id}[/]  {doc.filename}: {len(doc.pages)} page(s), {spans} spans, "
        f"{images} picture(s)  -> {path}"
    )
    _print_scan_reports(reports)


@app.command()
def plan(
    doc_id: list[str] = typer.Argument(..., help="One or more ingested document ids."),
    grade: int = typer.Option(..., min=1, max=5, help="Primary grades; use `run` for 6-9."),
    unit: str = typer.Option(..., help="Unit number, e.g. 1 (an id such as ta3.u1 also works)."),
    lesson: int = typer.Option(1, min=1, max=3),
    period: int = typer.Option(1, min=1, max=2),
    out: Path | None = typer.Option(None, help="Outline JSON path (default <workdir>/outlines/)."),
    no_refine: bool = typer.Option(False, "--no-refine", help="Skeleton only, no model call."),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
) -> None:
    """Outline one 35-minute period against the curriculum."""
    from classdeck.planning import outline as build_outline

    settings = _settings(provider, endpoint, model_id)
    sources = _load_sources(settings, doc_id)
    if len(sources) != len(doc_id):
        raise typer.Exit(1)
    unit_id = _unit_id(grade, unit)
    curriculum = _curriculum_for(settings, grade, unit_id, sources)
    model = None if no_refine else _generator(settings)
    result = build_outline(
        list(sources.values()),
        curriculum,
        unit_id=unit_id,
        lesson_no=lesson,
        period=period,
        grade=grade,
        provider=model,
    )
    target = out or settings.workdir / "outlines" / f"{unit_id}-l{lesson}-p{period}.outline.json"
    _write_text(target, result.model_dump_json(indent=2))
    table = Table(title=result.title, show_header=True, header_style="bold")
    for column in ("phase", "min", "activity", "spans", "title"):
        table.add_column(column)
    for section in result.sections:
        table.add_row(
            section.phase,
            str(section.minutes),
            str(section.activity_type or "-"),
            str(len(section.span_refs)),
            section.title,
        )
    console.print(table)
    console.print(f"outline -> {target}")


@app.command()
def draft(
    outline: Path = typer.Argument(..., exists=True, help="Outline JSON from `plan`."),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
    no_repair: bool = typer.Option(
        False, "--no-repair", help="Skip the repair round for blocks that fail the level checks."
    ),
) -> None:
    """Draft the lesson, verify grounding, run the level checks, save it to the store."""
    from classdeck.pipeline import build_lesson

    settings = _settings(provider, endpoint, model_id)
    plan_ = _load_outline(outline)
    sources = _load_sources(settings, plan_.doc_ids)
    curriculum = _curriculum(
        settings, plan_.grade, plan_.curriculum.version, ref=plan_.curriculum, sources=sources
    )
    model = _generator(settings)
    lesson = build_lesson(plan_, sources, curriculum, model, repair=not no_repair)
    store, _ = _stores(settings)
    path = store.save(lesson)
    _print_blocks(lesson)
    _print_warnings(lesson)
    console.print(f"lesson [green]{lesson.id}[/] -> {path}")


@app.command()
def check(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
) -> None:
    """Re-run grounding verification and the level checks; print the report and save."""
    settings = _settings(provider, endpoint, model_id)
    current, path = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    updated = _finish_lesson(current, sources, curriculum, _generator(settings))
    _save_lesson(updated, path)
    _print_blocks(updated)
    _print_warnings(updated)


@app.command()
def edit(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    instruction: str = typer.Argument(..., help='e.g. "Reduce new words to four"'),
    block: list[str] = typer.Option(..., "--block", "-b", help="Target block id (repeatable)."),
    apply: bool = typer.Option(False, "--apply", help="Accept the proposal and save."),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
) -> None:
    """Propose an edit to the selected blocks; preview before/after, affected blocks, checks."""
    from classdeck.editing import EditEngine, EditRejected, EditRequest
    from classdeck.llm import LLMError

    settings = _settings(provider, endpoint, model_id)
    model = _generator(settings)
    if model is None:
        console.print(
            "[red]Editing needs a model: set CLASSDECK_PROVIDER=api (or --provider api).[/]"
        )
        raise typer.Exit(1)
    current, path = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    engine = EditEngine(model, sources=sources, curriculum=curriculum)
    try:
        request = EditRequest(target_block_ids=block, instruction=instruction)
        proposal = engine.propose(current, request)
    except EditRejected as exc:
        console.print(f"[red]Could not produce a valid edit:[/] {'; '.join(exc.violations)}")
        if exc.rationale:
            console.print(f"  model rationale: {exc.rationale}")
        raise typer.Exit(1) from exc
    except LLMError as exc:
        console.print(f"[red]model call failed:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[bold]Rationale:[/] {proposal.rationale}")
    for block_id, replacement in proposal.replacements.items():
        _diff(current.blocks[block_id], replacement, f"Block {block_id}")
    for added in proposal.added_blocks:
        console.print(
            f"[green]+ new block {added.block.id} ({added.block.kind}) "
            f"after {added.after_block_id}[/]"
        )
    for removed in proposal.removed_block_ids:
        console.print(f"[red]- removed {removed}[/]")
    if proposal.affected:
        console.print("[bold]Affected (not changed; review or regenerate):[/]")
        for item in proposal.affected:
            console.print(f"  {item.block_id}: {item.reason} -> {item.suggested_action}")
    if proposal.checks_preview:
        console.print("[bold]Checks on the proposal:[/]")
        for preview in proposal.checks_preview:
            console.print(
                f"  {preview.block_id or 'lesson'} {preview.name} = {preview.status} "
                f"{preview.detail or ''}"
            )
    if not apply:
        console.print("Re-run with [bold]--apply[/] to accept (the model reply is cached).")
        return
    updated = engine.accept(current, proposal)
    _save_lesson(updated, path)
    console.print(f"[green]accepted[/] revision {updated.revisions[-1].id} -> {path}")


def _redo_path(path: Path) -> Path:
    return path.with_name(path.name.replace(".lesson.json", "") + ".redo.json")


@app.command()
def undo(lesson: str = typer.Argument(..., help="Lesson id or path.")) -> None:
    """Undo the last accepted edit (kept on a redo stack next to the lesson)."""
    from classdeck.editing import undo as undo_revision

    settings = _settings()
    current, path = _load_lesson(settings, lesson)
    try:
        restored, revision = undo_revision(current)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(1) from exc
    redo_file = _redo_path(path)
    stack = json.loads(redo_file.read_text(encoding="utf-8")) if redo_file.exists() else []
    stack.append(revision.model_dump(mode="json", by_alias=True))
    _write_text(redo_file, json.dumps(stack, indent=2, ensure_ascii=False))
    _save_lesson(restored, path)
    console.print(f"[green]undone[/] {revision.id} ({revision.instruction or 'direct edit'})")


@app.command()
def redo(lesson: str = typer.Argument(..., help="Lesson id or path.")) -> None:
    """Re-apply the last undone edit."""
    from classdeck.editing import redo as redo_revision
    from classdeck.lesson import Revision

    settings = _settings()
    current, path = _load_lesson(settings, lesson)
    redo_file = _redo_path(path)
    stack = json.loads(redo_file.read_text(encoding="utf-8")) if redo_file.exists() else []
    if not stack:
        console.print("[yellow]nothing to redo[/]")
        raise typer.Exit(1)
    revision = Revision.model_validate(stack.pop())
    _write_text(redo_file, json.dumps(stack, indent=2, ensure_ascii=False))
    _save_lesson(redo_revision(current, revision), path)
    console.print(f"[green]redone[/] {revision.id}")


@app.command(name="import-edits")
def import_edits(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    edited: Path = typer.Argument(
        ..., exists=True, readable=True, help="A deck saved from the browser with its edits."
    ),
    force: bool = typer.Option(
        False, "--force", help="Apply even if the deck was exported from another lesson."
    ),
) -> None:
    """Apply the edits embedded in a deck that was saved as a standalone file.

    The deck's Save button (outside the local app) writes the edit list into the page; this reads
    it and applies it as one revision (so `undo` takes it back), then re-checks and saves.
    """
    from classdeck.editing import FieldEditError, apply_deck_edits, read_embedded_edits

    settings = _settings()
    current, path = _load_lesson(settings, lesson)
    try:
        deck_lesson, edits = read_embedded_edits(edited.read_text(encoding="utf-8"))
    except (ValueError, FieldEditError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    if deck_lesson and deck_lesson != current.id and not force:
        console.print(
            f"[red]This deck was exported from lesson {deck_lesson}, not {current.id}.[/] "
            "Use --force to apply it anyway."
        )
        raise typer.Exit(1)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    try:
        result = apply_deck_edits(current, edits, sources=sources, curriculum=curriculum)
    except FieldEditError as exc:
        where = f" ({exc.block_id} {exc.field})" if exc.block_id else ""
        console.print(f"[red]{exc.message}{where}[/]")
        raise typer.Exit(1) from exc
    if not result.changed:
        console.print("[yellow]nothing to import: the lesson already has these edits[/]")
        return
    _save_lesson(result.lesson, path)
    _redo_path(path).unlink(missing_ok=True)  # a new edit ends the redo history
    console.print(
        f"[green]imported[/] {len(edits)} edit(s) into {len(result.changed_block_ids)} block(s): "
        f"revision {result.lesson.revisions[-1].id} -> {path}"
    )
    for notice in result.notices:
        console.print(f"  [yellow]{notice.message}[/]")


@app.command()
def illustrate(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    image_model: str | None = typer.Option(
        None, "--image-model", help="Image model id (default: CLASSDECK_IMAGE_MODEL)."
    ),
    max_items: int = typer.Option(8, "--max", min=1, help="Most new pictures to generate."),
    all_items: bool = typer.Option(
        False, "--all", help="Also draw function words (pronouns, prepositions, ...)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan and price; draw nothing."
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="api | mock (default: CLASSDECK_PROVIDER)."
    ),
) -> None:
    """Draw flashcard pictures for vocabulary items that have no textbook picture."""
    from classdeck.illustrate import STYLE_GUIDE
    from classdeck.illustrate import illustrate as run_illustrate
    from classdeck.llm.base import LLMError
    from classdeck.llm.images import build_image_provider
    from classdeck.llm.pricing import estimate_image_cost_usd

    if provider is not None and provider not in ("api", "mock"):
        console.print("[red]--provider must be api or mock[/]")
        raise typer.Exit(2)
    settings = _settings(provider, endpoint)
    if image_model:
        settings = settings.model_copy(update={"image_model": image_model})
    current, path = _load_lesson(settings, lesson)
    images = _image_root(settings)

    try:
        image_provider = build_image_provider(settings)
        plan = run_illustrate(
            current,
            provider=image_provider,
            image_root=images,
            max_items=max_items,
            all_items=all_items,
            dry_run=True,
        )
        for word, prompt in zip(plan.words, plan.prompts, strict=True):
            detail = prompt.removeprefix(STYLE_GUIDE).strip().replace("\n", " | ")
            console.print(f"  {word} -> {detail[:80]}")
        estimate = estimate_image_cost_usd(image_provider.model, plan.planned)
        price = "price unknown" if estimate is None else f"about ${estimate:.2f}"
        console.print(
            f"{plan.planned} new picture(s) with {image_provider.model} ({price}); "
            f"{plan.reused} already on disk, {plan.skipped} over the --max limit"
        )
        if dry_run:
            return
        report = run_illustrate(
            current,
            provider=image_provider,
            image_root=images,
            max_items=max_items,
            all_items=all_items,
        )
    except LLMError as exc:
        console.print(f"[red]illustrate failed:[/] {exc}")
        raise typer.Exit(1) from exc

    _save_lesson(report.lesson, path)
    cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.2f}"
    console.print(
        f"generated {report.generated}, reused {report.reused}, skipped {report.skipped}; "
        f"cost {cost}; lesson -> {path}"
    )


@app.command()
def export(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    html: bool = typer.Option(True, help="Write the HTML deck (the lesson)."),
    pptx: bool = typer.Option(False, help="Also write a PPTX copy (optional)."),
    docx: bool = typer.Option(False, help="Also write a DOCX lesson plan (optional)."),
    theme: str = typer.Option("sunny", help="HTML deck theme."),
    out_dir: Path | None = typer.Option(None, help="Default <workdir>/exports/<lesson id>/."),
) -> None:
    """Export the lesson as an HTML deck (PPTX / DOCX copies only on request)."""
    from classdeck.export import ExportBlocked, export_docx, export_html, export_pptx

    settings = _settings()
    current, _ = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    target = out_dir or settings.workdir / "exports" / current.id
    target.mkdir(parents=True, exist_ok=True)
    images = _image_root(settings)
    try:
        if html:
            path = export_html(
                current, target / "lesson.html", sources=sources, image_root=images, theme=theme
            )
            console.print(f"html -> {path}")
        if pptx:
            path = export_pptx(current, target / "lesson.pptx", sources=sources, image_root=images)
            console.print(f"pptx -> {path}")
        if docx:
            path = export_docx(
                current, target / "lesson.docx", sources=sources, curriculum=curriculum
            )
            console.print(f"docx -> {path}")
    except ExportBlocked as exc:
        console.print(f"[red]export blocked by failing checks:[/] {exc}")
        raise typer.Exit(1) from exc


# --------------------------------------------------------------------------- handoff


@handoff_app.command("deck")
def handoff_deck(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    out: Path | None = typer.Option(None, help="Default <workdir>/handoff/<id>.deck-pack.md"),
) -> None:
    """Write a prompt pack: paste it into a chat model to get an HTML deck back."""
    from classdeck.export import ExportBlocked
    from classdeck.handoff import build_deck_pack

    settings = _settings()
    current, _ = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    try:
        pack = build_deck_pack(current, sources=sources, curriculum=curriculum)
    except ExportBlocked as exc:
        console.print(f"[red]blocked by failing checks:[/] {exc}")
        raise typer.Exit(1) from exc
    target = out or settings.workdir / "handoff" / f"{current.id}.deck-pack.md"
    _write_text(target, pack)
    console.print(f"deck pack ({len(pack)} chars) -> {target}")


@handoff_app.command("check-deck")
def handoff_check_deck(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    deck: Path = typer.Argument(..., exists=True, help="The HTML file the chat model produced."),
    out: Path | None = typer.Option(None, help="Default <deck>.checked.html"),
) -> None:
    """Check a returned deck against the lesson and embed the real pictures."""
    from classdeck.handoff import check_deck

    settings = _settings()
    current, _ = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    curriculum = _curriculum(
        settings, current.grade, current.curriculum.version, ref=current.curriculum, sources=sources
    )
    result = check_deck(
        current,
        deck.read_text(encoding="utf-8"),
        sources=sources,
        image_root=_image_root(settings),
        curriculum=curriculum,
    )
    target = out or deck.with_suffix(".checked.html")
    _write_text(target, result.html)
    console.print(result.report())
    console.print(f"checked deck -> {target}")


@handoff_app.command("draft")
def handoff_draft(
    outline: Path = typer.Argument(..., exists=True, help="Outline JSON from `plan`."),
    out: Path | None = typer.Option(None, help="Default next to the outline."),
) -> None:
    """Write a prompt pack: paste it into a chat model to get the lesson blocks back as JSON."""
    from classdeck.handoff import build_draft_pack

    settings = _settings()
    plan_ = _load_outline(outline)
    sources = _load_sources(settings, plan_.doc_ids)
    curriculum = _curriculum(
        settings, plan_.grade, plan_.curriculum.version, ref=plan_.curriculum, sources=sources
    )
    pack = build_draft_pack(plan_, list(sources.values()), curriculum)
    stem = outline.name.replace(".outline.json", "")
    target = out or outline.with_name(stem + ".draft-pack.md")
    _write_text(target, pack)
    console.print(f"draft pack ({len(pack)} chars) -> {target}")


@handoff_app.command("import-draft")
def handoff_import_draft(
    outline: Path = typer.Argument(..., exists=True, help="Outline JSON from `plan`."),
    reply: Path = typer.Argument(..., exists=True, help="The chat model's reply (JSON array)."),
) -> None:
    """Validate a returned draft, fall back to templates where it fails, save the lesson."""
    from classdeck.handoff import import_draft

    settings = _settings()
    plan_ = _load_outline(outline)
    sources = _load_sources(settings, plan_.doc_ids)
    curriculum = _curriculum(
        settings, plan_.grade, plan_.curriculum.version, ref=plan_.curriculum, sources=sources
    )
    reply_text = reply.read_text(encoding="utf-8")
    result = import_draft(plan_, list(sources.values()), curriculum, reply_text)
    for report in result.section_reports:
        marker = "[green]model[/]" if report.status == "model" else "[yellow]template[/]"
        problems = f" ({'; '.join(report.problems)})" if report.problems else ""
        console.print(f"  {report.index}. {report.title}: {marker}{problems}")
    store, _ = _stores(settings)
    path = store.save(result.lesson)
    _print_blocks(result.lesson)
    _print_warnings(result.lesson)
    console.print(f"lesson [green]{result.lesson.id}[/] -> {path}")


# --------------------------------------------------------------------------- evaluation


def _eval_root(cases: Path) -> Path:
    """The directory that holds ``fixtures/``: case ``source`` paths are relative to it."""
    current = cases if cases.is_dir() else cases.parent
    while not (current / "fixtures").is_dir() and current.parent != current:
        current = current.parent
    return current


@app.command(name="eval")
def eval_cmd(
    cases: Path = typer.Argument(..., exists=True, help="A case file or a directory of cases."),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
    report: Path | None = typer.Option(None, help="Write a JSON results file."),
    judge: bool = typer.Option(True, help="Score with the judge model when one is configured."),
) -> None:
    """Run evaluation cases and print the metrics."""
    from classdeck.evaluation import load_cases, run_cases
    from classdeck.llm import build_provider

    settings = _settings(provider, endpoint, model_id)
    model = _generator(settings)
    judge_provider = build_provider(settings, "judge") if judge and model is not None else None
    eval_dir = _eval_root(cases)
    result = run_cases(
        load_cases(cases, eval_dir=eval_dir),
        settings=settings,
        provider=model,
        judge_provider=judge_provider,
        eval_dir=eval_dir,
        workdir=settings.workdir / "eval",
    )
    console.print(result.table())
    if report:
        result.write(report)
        console.print(f"report -> {report}")


@app.command(name="eval-compare")
def eval_compare(
    before: Path = typer.Argument(..., exists=True),
    after: Path = typer.Argument(..., exists=True),
) -> None:
    """Show metric deltas between two eval reports."""
    from classdeck.evaluation import compare

    console.print(compare(before, after))


# --------------------------------------------------------------------------- one-shot run


@app.command()
def run(
    file: Path = typer.Argument(..., exists=True, readable=True, help="PDF, DOCX or PPTX."),
    grade: int = typer.Option(..., min=1, max=9),
    unit: str = typer.Option(..., help="Unit number, e.g. 2 (an id such as ta3.u2 also works)."),
    lesson: int = typer.Option(
        1, min=1, max=7, help="Lesson 1-3 (grades 1-5) or the textbook lesson 1-7 (grades 6-9)."
    ),
    period: int | None = typer.Option(
        None, min=1, max=2, help="Period 1-2 of a primary lesson (default 1); not for grades 6-9."
    ),
    reference: Path | None = typer.Option(
        None,
        "--reference",
        exists=True,
        readable=True,
        help="Grades 6-9: a teacher's plan (.docx); its section for the lesson informs timing "
        "and the warm-up, the textbook still wins.",
    ),
    provider: str | None = typer.Option(None, help="Override CLASSDECK_PROVIDER (api|mock)."),
    endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_HELP),
    model_id: str | None = typer.Option(None, "--model", help=MODEL_HELP),
    pictures: bool | None = typer.Option(
        None,
        "--pictures/--no-pictures",
        help="Draw flashcard pictures for words without one (default: on for grades 1-5, off "
        "for grades 6-9).",
    ),
    max_pictures: int = typer.Option(4, "--max-pictures", min=0),
    open_deck: bool = typer.Option(True, "--open/--no-open", help="Open the deck in a browser."),
    no_refine: bool = typer.Option(False, "--no-refine", help="Skeleton outline, no model call."),
    no_repair: bool = typer.Option(False, "--no-repair", help="Skip the repair round."),
) -> None:
    """One shot: ingest -> plan -> draft -> pictures -> export, then open the deck.

    Grades 1-5 draft one 35-minute period (`--lesson 1-3`, `--period 1-2`). Grades 6-9 draft one
    textbook lesson of a unit (`--lesson 1-7`, one 45-minute period): the exercises come from the
    page, one model call adds the answers and the teacher's layer.
    """
    import webbrowser

    from classdeck.export import ExportBlocked, export_html
    from classdeck.ingest import UnsupportedFormat
    from classdeck.ingest import ingest as ingest_file
    from classdeck.llm.base import LLMError
    from classdeck.pipeline import build_lesson, build_secondary_lesson
    from classdeck.planning import outline as build_outline

    settings = _settings(provider, endpoint, model_id)
    model = _generator(settings)
    unit_id = _unit_id(grade, unit)
    secondary = grade >= 6
    if secondary and period is not None:
        console.print("[red]--period does not apply to grades 6-9: use --lesson 1-7.[/]")
        raise typer.Exit(2)
    if not secondary and lesson > 3:
        console.print("[red]--lesson must be 1-3 for grades 1-5.[/]")
        raise typer.Exit(2)
    if reference is not None and not secondary:
        console.print("[red]--reference is for grades 6-9.[/]")
        raise typer.Exit(2)
    period = period or 1
    where = "offline template pipeline" if model is None else f"{model.name} / {model.model}"
    place = f"lesson {lesson}" if secondary else f"lesson {lesson}, period {period}"
    console.print(
        f"[bold]ClassDeck[/] grade {grade}, unit {unit_id.rsplit('.u', 1)[1]}, {place}: {where}"
    )
    if pictures is None:
        pictures = not secondary

    try:
        console.print("[1/5] ingest")
        scan_reports: list[Any] = []
        doc = ingest_file(
            file,
            image_dir=_image_root(settings),
            provider=model,
            report=scan_reports.append,
        )
        _print_scan_reports(scan_reports)
        store, sources_store = _stores(settings)
        sources_store.save(doc)
        curriculum = _curriculum_for(settings, grade, unit_id, {doc.doc_id: doc})

        if secondary:
            console.print("[2/5] reference plan" if reference else "[2/5] lesson plan")
            reference_text = ""
            if reference is not None:
                from classdeck.planning.reference import reference_for_lesson

                reference_text = reference_for_lesson(reference, lesson)
                console.print(
                    f"      reference: {len(reference_text)} characters for lesson {lesson}"
                    if reference_text
                    else f"      [yellow]no section for lesson {lesson} in {reference.name}[/]"
                )
            console.print("[3/5] draft, grounding and level checks")
            result, report = build_secondary_lesson(
                {doc.doc_id: doc},
                curriculum,
                model,
                unit_id=unit_id,
                lesson_no=lesson,
                reference=reference_text,
            )
            _print_secondary_report(report)
        else:
            console.print("[2/5] outline")
            plan_ = build_outline(
                [doc],
                curriculum,
                unit_id=unit_id,
                lesson_no=lesson,
                period=period,
                grade=grade,
                provider=None if no_refine else model,
            )
            _write_text(
                settings.workdir / "outlines" / f"{unit_id}-l{lesson}-p{period}.outline.json",
                plan_.model_dump_json(indent=2),
            )
            console.print("[3/5] draft, grounding and level checks")
            result = build_lesson(plan_, {doc.doc_id: doc}, curriculum, model, repair=not no_repair)
        path = store.save(result)

        if pictures and max_pictures > 0:
            from classdeck.illustrate import illustrate as run_illustrate
            from classdeck.llm.images import build_image_provider

            console.print("[4/5] flashcard pictures")
            illustrated = run_illustrate(
                result,
                provider=build_image_provider(settings),
                image_root=_image_root(settings),
                max_items=max_pictures,
            )
            result = illustrated.lesson
            path = _save_lesson(result, path)
            cost = "" if illustrated.cost_usd is None else f", about ${illustrated.cost_usd:.2f}"
            console.print(f"      {illustrated.generated} drawn, {illustrated.reused} reused{cost}")
        else:
            console.print("[4/5] flashcard pictures skipped")

        console.print("[5/5] export")
        target = settings.workdir / "exports" / result.id
        target.mkdir(parents=True, exist_ok=True)
        sources = {doc.doc_id: doc}
        images = _image_root(settings)
        deck = export_html(result, target / "lesson.html", sources=sources, image_root=images)
    except (UnsupportedFormat, LLMError, ExportBlocked, KeyError, ValueError) as exc:
        console.print(f"[red]run failed:[/] {exc}")
        raise typer.Exit(1) from exc

    _print_blocks(result)
    _print_warnings(result)
    console.print(f"lesson [green]{result.id}[/] -> {path}")
    console.print(f"deck   -> {deck}")
    console.print(
        "Open the deck: arrows move, tap reveals, T = teacher mode, N = notes, F = fullscreen."
    )
    if open_deck:
        webbrowser.open(deck.resolve().as_uri())


def _print_secondary_report(report: Any) -> None:
    """What the drafting call of a secondary lesson did, in a few lines."""
    if not report.model_called:
        console.print("      no model: exercises without answer keys, template warm-up and wrap-up")
        return
    note = "re-prompted once" if report.reprompted else "first reply used"
    cost = "n/a" if report.cost_usd is None else f"${report.cost_usd:.4f}"
    state = "cached" if report.cached and not report.input_tokens else cost
    console.print(
        f"      drafting call: {note}; {report.input_tokens} in / {report.output_tokens} out "
        f"tokens, {state}"
    )
    if report.error:
        console.print(f"      [yellow]no usable reply:[/] {report.error}")
    for key, found in report.problems.items():
        console.print(f"      [yellow]problem[/] {key}: {'; '.join(found)}")
    if report.fallback_exercises:
        console.print(
            "      question list without answers for: " + ", ".join(report.fallback_exercises)
        )
    if report.fallback_extras:
        console.print(
            "      [yellow]left out (no usable reply):[/] " + ", ".join(report.fallback_extras)
        )
    if report.fallback_warm_up or report.fallback_wrap_up:
        parts = [
            n
            for n, used in (
                ("warm-up", report.fallback_warm_up),
                ("wrap-up", report.fallback_wrap_up),
            )
            if used
        ]
        console.print(f"      template used for: {', '.join(parts)}")


# --------------------------------------------------------------------------- gallery and app


@app.command()
def gallery(
    lesson: str = typer.Argument(..., help="Lesson id or path."),
    theme: list[str] = typer.Option(None, "--theme", "-t", help="Theme id (repeatable)."),
    open_page: bool = typer.Option(True, "--open/--no-open", help="Open the gallery in a browser."),
) -> None:
    """Render one lesson in every theme, side by side, to choose and rate a style."""
    import webbrowser

    from classdeck.export import ExportBlocked
    from classdeck.export.gallery import build_gallery

    settings = _settings()
    current, _ = _load_lesson(settings, lesson)
    sources = _load_sources(settings, (ref.doc_id for ref in current.sources))
    target = settings.workdir / "gallery" / current.id
    try:
        index = build_gallery(
            current, target, sources=sources, image_root=_image_root(settings), themes=theme or None
        )
    except (ExportBlocked, ValueError) as exc:
        console.print(f"[red]gallery failed:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"gallery -> {index}")
    if open_page:
        webbrowser.open(index.resolve().as_uri())


@app.command()
def serve(
    port: int = typer.Option(8765, help="Port on 127.0.0.1."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the app in a browser."),
) -> None:
    """Start the local teacher app (http://127.0.0.1:PORT). Needs the `api` extra."""
    try:
        from classdeck.app.server import serve as run_server
    except ImportError as exc:
        console.print('[red]The app needs the api extra:[/] pip install -e ".[api]"')
        raise typer.Exit(1) from exc
    run_server(host="127.0.0.1", port=port, open_browser=open_browser)


if __name__ == "__main__":
    app()
