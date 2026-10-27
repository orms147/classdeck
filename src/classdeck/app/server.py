"""The local teacher app: a FastAPI factory, the JSON routes and the static page.

``create_app(settings)`` builds the app, ``serve()`` runs it on the loopback interface. The
routes call the same library functions as the CLI (``docs/local-app.md``); long pipeline runs
happen in background threads (:mod:`classdeck.app.jobs`) and are polled by the page.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from classdeck import __version__
from classdeck.app.jobs import Job, JobRegistry, ProposalRegistry
from classdeck.config import ENDPOINTS, Settings, get_settings
from classdeck.curriculum import Curriculum, list_available, load_curriculum
from classdeck.editing import EditEngine, EditProposal, EditRejected, EditRequest
from classdeck.editing import redo as redo_revision
from classdeck.editing import undo as undo_revision
from classdeck.english.presets import EDIT_PRESETS
from classdeck.export import ExportBlocked, export_docx, export_html, export_pptx
from classdeck.export.html import available_themes, render_html
from classdeck.ingest import UnsupportedFormat
from classdeck.ingest import ingest as ingest_file
from classdeck.lesson import (
    Block,
    CheckStatus,
    Lesson,
    LessonStore,
    Revision,
    SourceDocument,
    SourceStore,
)
from classdeck.lesson.store import LessonNotFound
from classdeck.llm.base import LLMError, LLMProvider
from classdeck.llm.images import ImageProvider, build_image_provider
from classdeck.llm.pricing import estimate_image_cost_usd

logger = logging.getLogger(__name__)

CURRICULUM_VERSION = "gdpt-2018"
OFFLINE = "offline"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
UPLOAD_MAGIC = {".pdf": b"%PDF", ".docx": b"PK", ".pptx": b"PK"}
DEFAULT_MAX_PICTURES = 4
DEFAULT_THEME = "sunny"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}

ProviderFactory = Callable[[Settings, str | None], LLMProvider | None]
"""``(settings, endpoint) -> provider``; ``endpoint`` is ``None`` (the configured default),
``"offline"`` or a name from :data:`~classdeck.config.ENDPOINTS`. ``None`` means no model."""

ImageProviderFactory = Callable[[Settings, str | None], ImageProvider]

_EXPORT_TYPES = {
    "html": "text/html; charset=utf-8",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# --------------------------------------------------------------------------- factories


def default_provider_factory(settings: Settings, endpoint: str | None) -> LLMProvider | None:
    """The configured model, or ``None`` for the offline template pipeline."""
    if endpoint == OFFLINE or (endpoint is None and settings.provider == "mock"):
        return None
    resolved = settings.resolved(endpoint)
    if resolved.provider == "mock":
        return None
    from classdeck.llm import build_provider

    return build_provider(resolved)


def default_image_factory(settings: Settings, endpoint: str | None) -> ImageProvider:
    resolved = settings if endpoint in (None, OFFLINE) else settings.resolved(endpoint)
    return build_image_provider(resolved)


# --------------------------------------------------------------------------- request bodies


class ProposalBody(BaseModel):
    block_ids: list[str]
    instruction: str
    endpoint: str | None = None


class IllustrateBody(BaseModel):
    max: int = DEFAULT_MAX_PICTURES
    endpoint: str | None = None
    dry_run: bool = False
    all_items: bool = False


class CheckBody(BaseModel):
    endpoint: str | None = None


class JobParams(BaseModel):
    path: Path
    grade: int
    unit: str
    lesson_no: int
    period: int
    endpoint: str | None
    pictures: bool
    max_pictures: int
    theme: str


class ApiError(Exception):
    """An error the page shows to the teacher: ``{"error": message}`` with this status."""

    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class _NoModel:
    """Stands in for the provider when only ``accept`` is used: any call is a bug."""

    name = "none"
    model = "none"

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("no model is configured for this operation")


# --------------------------------------------------------------------------- helpers


def _theme_infos() -> list[dict[str, str]]:
    """The deck themes as ``{id, label, description, suits}``.

    ``available_themes()`` returns plain names in older versions of the export package and
    manifest records (``id``, ``label``, ...) in newer ones; both are accepted.
    """
    infos: list[dict[str, str]] = []
    for theme in available_themes():
        if isinstance(theme, str):
            infos.append({"id": theme, "label": theme.title(), "description": "", "suits": ""})
        else:
            infos.append(
                {
                    "id": theme.id,
                    "label": theme.label,
                    "description": theme.description,
                    "suits": theme.suits,
                }
            )
    return infos


def _theme_ids() -> list[str]:
    return [info["id"] for info in _theme_infos()]


def _static_dir() -> Path:
    return Path(str(resources.files("classdeck.app").joinpath("static")))


def _default_sample_dir() -> Path | None:
    for candidate in (
        Path(__file__).resolve().parents[3] / "eval" / "fixtures" / "public",
        Path("eval/fixtures/public"),
    ):
        if candidate.is_dir():
            return candidate
    return None


def _block_label(block: Block) -> str:
    content: Any = block.content
    match block.kind:
        case "objective" | "teacher_note":
            text = content.text
        case "vocabulary":
            words = [item.word for item in content.items]
            text = f"{len(words)}: " + ", ".join(words[:4]) + (" ..." if len(words) > 4 else "")
        case "sentence_pattern":
            text = content.pattern
        case "dialogue":
            text = content.lines[0].text if content.lines else (content.context or "")
        case "phonics":
            text = f"/{content.sound}/ {content.letters}"
        case "activity":
            text = content.title
        case "summary":
            text = content.bullets[0] if content.bullets else ""
        case _:
            text = content.caption or content.alt
    text = " ".join(str(text).split())
    return text if len(text) <= 70 else text[:67] + "..."


def _block_status(block: Block) -> str:
    statuses = {check.status for check in block.checks}
    for status in (CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.STALE):
        if status in statuses:
            return status.value
    return CheckStatus.PASS.value


def _count_issues(lesson: Lesson) -> tuple[int, int]:
    """``(warnings, failures)`` over lesson-level and block-level checks."""
    results = [*lesson.checks, *(c for block in lesson.blocks.values() for c in block.checks)]
    warns = sum(1 for c in results if c.status is CheckStatus.WARN)
    fails = sum(1 for c in results if c.status is CheckStatus.FAIL)
    return warns, fails


def _citations(block: Block, sources: Mapping[str, SourceDocument]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for citation in block.sources:
        document = sources.get(citation.doc_id)
        span = document.find_span(citation.page, citation.span_id) if document else None
        found.append(
            {
                "doc_id": citation.doc_id,
                "filename": document.filename if document else None,
                "page": citation.page,
                "span_id": citation.span_id,
                "quote": citation.quote or (span.text if span else None),
            }
        )
    return found


def _redo_path(path: Path) -> Path:
    """Same redo-stack file as the CLI, so the two can be mixed."""
    return path.with_name(path.name.replace(".lesson.json", "") + ".redo.json")


def _read_redo(path: Path) -> list[dict[str, Any]]:
    redo_file = _redo_path(path)
    if not redo_file.exists():
        return []
    try:
        data = json.loads(redo_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_redo(path: Path, stack: list[dict[str, Any]]) -> None:
    redo_file = _redo_path(path)
    if not stack:
        redo_file.unlink(missing_ok=True)
        return
    redo_file.parent.mkdir(parents=True, exist_ok=True)
    redo_file.write_text(json.dumps(stack, indent=2, ensure_ascii=False), encoding="utf-8")


def _revision_summary(revision: Revision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "at": revision.at.isoformat(),
        "actor": revision.actor,
        "instruction": revision.instruction,
        "target_block_ids": revision.target_block_ids,
        "flagged_block_ids": revision.flagged_block_ids,
    }


class AppState:
    """What the routes share: settings, factories, registries and per-lesson locks."""

    def __init__(
        self,
        settings: Settings,
        provider_factory: ProviderFactory,
        image_factory: ImageProviderFactory,
        sample_dir: Path | None,
    ) -> None:
        self.settings = settings
        self.provider_factory = provider_factory
        self.image_factory = image_factory
        self.sample_dir = sample_dir
        self.jobs = JobRegistry()
        self.proposals = ProposalRegistry()
        self.lessons = LessonStore(settings.workdir)
        self.sources = SourceStore(settings.workdir)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._curricula: dict[tuple[str, int], Curriculum] = {}

    @property
    def image_root(self) -> Path:
        return self.settings.workdir / "images"

    @contextmanager
    def locked(self, lesson_id: str) -> Iterator[None]:
        with self._locks_guard:
            lock = self._locks.setdefault(lesson_id, threading.RLock())
        with lock:
            yield

    # -- endpoints and models

    def endpoint_configured(self, name: str) -> bool:
        if name == OFFLINE:
            return True
        key = getattr(self.settings, f"{name}_api_key", None)
        return bool(key and key.get_secret_value())

    def default_configured(self) -> bool:
        key = self.settings.resolved().model_api_key
        return self.settings.provider == "api" and bool(key and key.get_secret_value())

    def normalise_endpoint(self, raw: str | None) -> str | None:
        """``None`` for the default endpoint, ``"offline"``, or a configured profile name."""
        name = (raw or "").strip().lower()
        if name in ("", "default"):
            return None
        if name == OFFLINE:
            return OFFLINE
        if name not in ENDPOINTS:
            raise ApiError(400, f"Unknown endpoint {raw!r}.")
        if not self.endpoint_configured(name):
            raise ApiError(400, f"The {name} endpoint has no API key configured.")
        return name

    def provider_for(self, raw: str | None) -> LLMProvider | None:
        endpoint = self.normalise_endpoint(raw)
        try:
            return self.provider_factory(self.settings, endpoint)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc

    def default_endpoint(self) -> str:
        configured = self.settings.endpoint
        if configured and self.endpoint_configured(configured):
            return configured
        if self.settings.provider == "mock":
            return OFFLINE
        if self.default_configured():
            return "default"
        return next((n for n in ENDPOINTS if self.endpoint_configured(n)), OFFLINE)

    # -- data

    def curriculum(self, grade: int, version: str = CURRICULUM_VERSION) -> Curriculum:
        key = (version, grade)
        if key not in self._curricula:
            try:
                self._curricula[key] = load_curriculum(self.settings.curriculum_dir, version, grade)
            except (FileNotFoundError, ValueError) as exc:
                raise ApiError(400, str(exc)) from exc
        return self._curricula[key]

    def load_lesson(self, lesson_id: str) -> Lesson:
        try:
            return self.lessons.load(lesson_id)
        except (LessonNotFound, ValueError) as exc:
            raise ApiError(404, f"No lesson {lesson_id!r}.") from exc

    def sources_of(self, lesson: Lesson) -> dict[str, SourceDocument]:
        found: dict[str, SourceDocument] = {}
        for ref in lesson.sources:
            try:
                found[ref.doc_id] = self.sources.load(ref.doc_id)
            except (FileNotFoundError, ValueError):
                logger.warning("source %s not found for lesson %s", ref.doc_id, lesson.id)
        return found

    # -- samples

    def samples(self) -> list[dict[str, Any]]:
        if self.sample_dir is None:
            return []
        found: list[dict[str, Any]] = []
        for pdf in sorted(self.sample_dir.glob("*.pdf")):
            meta_file = self.sample_dir / "units" / f"{pdf.stem}.json"
            if not meta_file.is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                grade, unit = int(meta["grade"]), int(meta["unit"])
            except (OSError, ValueError, KeyError):
                continue
            found.append(
                {
                    "id": pdf.stem,
                    "title": str(meta.get("title", pdf.stem)),
                    "grade": grade,
                    "unit": unit,
                    "unit_id": f"ta{grade}.u{unit}",
                    "filename": pdf.name,
                }
            )
        return found

    def sample_path(self, sample_id: str) -> Path:
        for sample in self.samples():
            if sample["id"] == sample_id and self.sample_dir is not None:
                return self.sample_dir / sample["filename"]
        raise ApiError(400, f"Unknown sample {sample_id!r}.")


# --------------------------------------------------------------------------- payloads


def lesson_payload(state: AppState, lesson: Lesson) -> dict[str, Any]:
    """The lesson JSON plus what the three panes need, so the page makes one request."""
    sources = state.sources_of(lesson)
    data = lesson.model_dump(mode="json")
    data["revisions"] = [_revision_summary(r) for r in lesson.revisions]
    rows = []
    for section in lesson.sections:
        for block_id in section.block_ids:
            block = lesson.blocks[block_id]
            rows.append(
                {
                    "id": block.id,
                    "kind": block.kind,
                    "section_id": section.id,
                    "phase": section.phase.value,
                    "label": _block_label(block),
                    "provenance": block.provenance.value,
                    "status": _block_status(block),
                    "citations": _citations(block, sources),
                }
            )
    warns, fails = _count_issues(lesson)
    return {
        "lesson": data,
        "summary": {
            "blocks": rows,
            "sections": [
                {"id": s.id, "phase": s.phase.value, "title": s.title, "minutes": s.minutes}
                for s in lesson.sections
            ],
            "warnings": warns,
            "failures": fails,
            "can_undo": bool(lesson.revisions),
            "can_redo": bool(_read_redo(state.lessons.path_for(lesson.id))),
        },
    }


def proposal_payload(key: str, lesson_id: str, proposal: EditProposal) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for block_id, replacement in proposal.replacements.items():
        changes.append(
            {
                "op": "replace",
                "block_id": block_id,
                "before": proposal.before.blocks[block_id].model_dump(mode="json"),
                "after": replacement.model_dump(mode="json"),
            }
        )
    for added in proposal.added_blocks:
        changes.append(
            {
                "op": "add",
                "block_id": added.block.id,
                "after_block_id": added.after_block_id,
                "before": None,
                "after": added.block.model_dump(mode="json"),
            }
        )
    for block_id in proposal.removed_block_ids:
        changes.append(
            {
                "op": "remove",
                "block_id": block_id,
                "before": proposal.before.blocks[block_id].model_dump(mode="json"),
                "after": None,
            }
        )
    return {
        "id": key,
        "lesson_id": lesson_id,
        "instruction": proposal.instruction,
        "rationale": proposal.rationale,
        "target_block_ids": proposal.target_block_ids,
        "changes": changes,
        "affected": [a.model_dump(mode="json") for a in proposal.affected],
        "checks_preview": [c.model_dump(mode="json") for c in proposal.checks_preview],
    }


# --------------------------------------------------------------------------- the job


def _save_upload(state: AppState, file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in UPLOAD_MAGIC:
        raise ApiError(400, "Only PDF, DOCX and PPTX files can be used.")
    folder = state.settings.workdir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                if size == 0 and not chunk.startswith(UPLOAD_MAGIC[suffix]):
                    raise ApiError(400, f"The file does not look like a {suffix[1:].upper()}.")
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "The file is larger than 30 MB.")
                handle.write(chunk)
        if size == 0:
            raise ApiError(400, "The file is empty.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def run_job(state: AppState, job: Job, params: JobParams) -> None:
    """ingest -> outline -> draft -> pictures -> export, as ``classdeck run``."""
    from classdeck.pipeline import build_lesson
    from classdeck.planning import outline as build_outline

    settings = state.settings
    try:
        model = state.provider_for(params.endpoint)
        job.begin("ingest")
        doc = ingest_file(params.path, image_dir=state.image_root)
        state.sources.save(doc)
        curriculum = state.curriculum(params.grade)
        sources = {doc.doc_id: doc}
        job.finish_stage("ingest")

        job.begin("outline")
        plan = build_outline(
            [doc],
            curriculum,
            unit_id=params.unit,
            lesson_no=params.lesson_no,
            period=params.period,
            grade=params.grade,
            provider=model,
        )
        outlines = settings.workdir / "outlines"
        outlines.mkdir(parents=True, exist_ok=True)
        name = f"{params.unit}-l{params.lesson_no}-p{params.period}.outline.json"
        (outlines / name).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        job.finish_stage("outline")

        job.begin("draft", "draft")
        lesson = build_lesson(plan, sources, curriculum, model, on_stage=job.note)
        state.lessons.save(lesson)
        job.finish_stage("draft")

        if params.pictures and params.max_pictures > 0:
            from classdeck.illustrate import illustrate as run_illustrate

            job.begin("pictures")
            try:
                provider = state.image_factory(settings, state.normalise_endpoint(params.endpoint))
                report = run_illustrate(
                    lesson,
                    provider=provider,
                    image_root=state.image_root,
                    max_items=params.max_pictures,
                )
                lesson = report.lesson
                state.lessons.save(lesson)
            except (LLMError, ValueError) as exc:
                job.warn(f"pictures: {exc}")
            job.finish_stage("pictures")
        else:
            job.finish_stage("pictures", skipped=True)

        job.begin("export")
        target = settings.workdir / "exports" / lesson.id
        target.mkdir(parents=True, exist_ok=True)
        try:
            images = state.image_root
            export_html(
                lesson,
                target / "lesson.html",
                sources=sources,
                image_root=images,
                theme=params.theme,
            )
            export_pptx(lesson, target / "lesson.pptx", sources=sources, image_root=images)
            export_docx(lesson, target / "lesson.docx", sources=sources, curriculum=curriculum)
        except ExportBlocked as exc:
            job.warn(f"export: {exc}")
        job.finish_stage("export")
        job.succeed(lesson.id)
    except ApiError as exc:
        job.fail(exc.message)
    except KeyError as exc:
        job.fail(str(exc.args[0]) if exc.args else "Unknown unit.")
    except (LLMError, UnsupportedFormat, ValueError, OSError) as exc:
        logger.warning("job %s failed: %s", job.id, exc)
        job.fail(str(exc))


# --------------------------------------------------------------------------- the app


def create_app(
    settings: Settings | None = None,
    *,
    provider_factory: ProviderFactory | None = None,
    image_factory: ImageProviderFactory | None = None,
    sample_dir: Path | None = None,
) -> FastAPI:
    """Build the app. The factories are the seams tests use to script the model."""
    state = AppState(
        settings or get_settings(),
        provider_factory or default_provider_factory,
        image_factory or default_image_factory,
        sample_dir if sample_dir is not None else _default_sample_dir(),
    )
    app = FastAPI(title="ClassDeck", version=__version__, docs_url=None, redoc_url=None)
    app.state.classdeck = state

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("host", "")
        host = header.split("]")[0].lstrip("[") if header.startswith("[") else header.split(":")[0]
        if host and host not in LOOPBACK_HOSTS:
            return JSONResponse({"error": "This app only answers on localhost."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.message, **exc.extra}, status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(str(e["loc"][-1]) for e in exc.errors() if e.get("loc"))
        return JSONResponse({"error": f"Invalid request: {fields}"}, status_code=422)

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse({"error": f"The model call failed: {exc}"}, status_code=502)

    # -- config and lists

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        cfg = state.settings
        endpoints: list[dict[str, Any]] = [
            {"name": OFFLINE, "label": "Offline", "configured": True, "model": None}
        ]
        for name, profile in ENDPOINTS.items():
            endpoints.append(
                {
                    "name": name,
                    "label": profile.label,
                    "configured": state.endpoint_configured(name),
                    "model": profile.default_model,
                }
            )
        if state.default_configured():
            endpoints.append(
                {
                    "name": "default",
                    "label": cfg.model_api_host,
                    "configured": True,
                    "model": cfg.model,
                }
            )
        grades = []
        for version, grade in list_available(cfg.curriculum_dir):
            if version != CURRICULUM_VERSION:
                continue
            units = [
                {"id": u.id, "title": u.title, "lessons": u.lessons}
                for u in state.curriculum(grade).file.units
            ]
            grades.append({"grade": grade, "units": units})
        return {
            "version": __version__,
            "provider_mode": cfg.provider,
            "endpoints": endpoints,
            "default_endpoint": state.default_endpoint(),
            "themes": _theme_infos(),
            "default_theme": DEFAULT_THEME,
            "grades": grades,
            "samples": state.samples(),
            "presets": [
                {
                    "id": p.id,
                    "label_en": p.label_en,
                    "label_vi": p.label_vi,
                    "instruction": p.instruction,
                    "takes_number": p.takes_number,
                    "applies_to": [k.value for k in p.applies_to],
                }
                for p in EDIT_PRESETS
            ],
            "pictures": {
                "model": cfg.image_model,
                "price_per_picture_usd": estimate_image_cost_usd(cfg.image_model, 1),
                "default_max": DEFAULT_MAX_PICTURES,
            },
            "limits": {"upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)},
        }

    @app.get("/api/lessons")
    def list_lessons() -> dict[str, Any]:
        rows = []
        for lesson_id in state.lessons.list_ids():
            try:
                lesson = state.lessons.load(lesson_id)
            except (ValueError, OSError):
                continue
            warns, fails = _count_issues(lesson)
            rows.append(
                {
                    "id": lesson.id,
                    "title": lesson.title,
                    "grade": lesson.grade,
                    "unit_id": lesson.curriculum.unit_id,
                    "lesson_no": lesson.curriculum.lesson_no,
                    "period": lesson.curriculum.period,
                    "updated_at": lesson.updated_at.isoformat(),
                    "created_at": lesson.created_at.isoformat(),
                    "blocks": len(lesson.blocks),
                    "warnings": warns,
                    "failures": fails,
                }
            )
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return {"lessons": rows}

    # -- jobs

    @app.post("/api/jobs")
    def create_job(
        file: UploadFile | None = File(default=None),  # noqa: B008
        sample: str | None = Form(default=None),
        grade: int = Form(...),
        unit: str = Form(...),
        lesson: int = Form(default=1),
        period: int = Form(default=1),
        endpoint: str | None = Form(default=None),
        pictures: bool = Form(default=False),
        max_pictures: int = Form(default=DEFAULT_MAX_PICTURES),
        theme: str = Form(default=DEFAULT_THEME),
    ) -> dict[str, str]:
        if grade not in (3, 4, 5):
            raise ApiError(400, "Grade must be 3, 4 or 5.")
        if lesson not in (1, 2, 3) or period not in (1, 2):
            raise ApiError(400, "Lesson must be 1-3 and period 1-2.")
        if theme not in _theme_ids():
            raise ApiError(400, f"Unknown theme {theme!r}.")
        state.normalise_endpoint(endpoint)
        try:
            state.curriculum(grade).unit(unit)
        except KeyError as exc:
            raise ApiError(400, str(exc.args[0])) from exc
        if file is not None and file.filename:
            path = _save_upload(state, file)
        elif sample:
            path = state.sample_path(sample)
        else:
            raise ApiError(400, "Choose a file or a sample unit.")
        params = JobParams(
            path=path,
            grade=grade,
            unit=unit,
            lesson_no=lesson,
            period=period,
            endpoint=endpoint,
            pictures=pictures,
            max_pictures=max(0, min(max_pictures, 24)),
            theme=theme,
        )
        job = state.jobs.start(lambda j: run_job(state, j, params))
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = state.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "Unknown job.")
        return job.snapshot()

    # -- lessons

    def theme_of(theme: str | None) -> str:
        name = theme or DEFAULT_THEME
        if name not in _theme_ids():
            raise ApiError(400, f"Unknown theme {name!r}.")
        return name

    @app.get("/api/lessons/{lesson_id}")
    def get_lesson(lesson_id: str) -> dict[str, Any]:
        return lesson_payload(state, state.load_lesson(lesson_id))

    @app.get("/api/lessons/{lesson_id}/deck")
    def get_deck(lesson_id: str, theme: str | None = None) -> HTMLResponse:
        lesson = state.load_lesson(lesson_id)
        try:
            html = render_html(
                lesson,
                sources=state.sources_of(lesson),
                image_root=state.image_root,
                theme=theme_of(theme),
            )
        except ExportBlocked as exc:
            raise ApiError(409, f"The deck is blocked by failing checks: {exc}") from exc
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/api/lessons/{lesson_id}/export/{fmt}")
    def export(lesson_id: str, fmt: str, theme: str | None = None) -> FileResponse:
        if fmt not in _EXPORT_TYPES:
            raise ApiError(404, f"Unknown export format {fmt!r}.")
        lesson = state.load_lesson(lesson_id)
        sources = state.sources_of(lesson)
        target = state.settings.workdir / "exports" / lesson.id
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"lesson.{fmt}"
        try:
            if fmt == "html":
                export_html(
                    lesson,
                    path,
                    sources=sources,
                    image_root=state.image_root,
                    theme=theme_of(theme),
                )
            elif fmt == "pptx":
                export_pptx(lesson, path, sources=sources, image_root=state.image_root)
            else:
                curriculum = state.curriculum(lesson.grade, lesson.curriculum.version)
                export_docx(lesson, path, sources=sources, curriculum=curriculum)
        except ExportBlocked as exc:
            raise ApiError(409, f"The export is blocked by failing checks: {exc}") from exc
        return FileResponse(
            path,
            media_type=_EXPORT_TYPES[fmt],
            filename=f"{lesson.id}.{fmt}",
            headers={"Cache-Control": "no-store"},
        )

    # -- editing

    @app.post("/api/lessons/{lesson_id}/proposals")
    def create_proposal(lesson_id: str, body: ProposalBody) -> dict[str, Any]:
        lesson = state.load_lesson(lesson_id)
        unknown = [b for b in body.block_ids if b not in lesson.blocks]
        if unknown or not body.block_ids:
            raise ApiError(400, f"Unknown or missing blocks: {', '.join(unknown) or 'none'}")
        if not body.instruction.strip():
            raise ApiError(400, "Write an instruction first.")
        model = state.provider_for(body.endpoint)
        if model is None:
            raise ApiError(400, "Editing needs a model: choose an endpoint other than Offline.")
        engine = EditEngine(
            model,
            sources=state.sources_of(lesson),
            curriculum=state.curriculum(lesson.grade, lesson.curriculum.version),
        )
        try:
            request = EditRequest(target_block_ids=body.block_ids, instruction=body.instruction)
            proposal = engine.propose(lesson, request)
        except EditRejected as exc:
            raise ApiError(
                422,
                "The model could not produce a valid edit: " + "; ".join(exc.violations),
                violations=exc.violations,
                rationale=exc.rationale,
            ) from exc
        except ValidationError as exc:
            raise ApiError(400, "Invalid edit request.") from exc
        key = state.proposals.add(lesson_id, proposal)
        return proposal_payload(key, lesson_id, proposal)

    @app.post("/api/lessons/{lesson_id}/proposals/{proposal_id}/accept")
    def accept_proposal(lesson_id: str, proposal_id: str) -> dict[str, Any]:
        stored = state.proposals.get(proposal_id, lesson_id)
        if stored is None:
            raise ApiError(404, "This proposal is no longer available. Propose the edit again.")
        with state.locked(lesson_id):
            lesson = state.load_lesson(lesson_id)
            # Accepting applies a finished proposal; no model call happens.
            engine = EditEngine(
                _NoModel(),
                sources=state.sources_of(lesson),
                curriculum=state.curriculum(lesson.grade, lesson.curriculum.version),
            )
            try:
                updated = engine.accept(lesson, stored.proposal)
            except ValueError as exc:
                state.proposals.discard(proposal_id)
                raise ApiError(409, f"The lesson changed since this proposal: {exc}") from exc
            state.lessons.save(updated)
            _write_redo(state.lessons.path_for(lesson_id), [])
        state.proposals.discard(proposal_id)
        return lesson_payload(state, updated)

    @app.delete("/api/lessons/{lesson_id}/proposals/{proposal_id}")
    def discard_proposal(lesson_id: str, proposal_id: str) -> dict[str, bool]:
        if state.proposals.get(proposal_id, lesson_id) is not None:
            state.proposals.discard(proposal_id)
        return {"ok": True}

    @app.post("/api/lessons/{lesson_id}/undo")
    def undo(lesson_id: str) -> dict[str, Any]:
        with state.locked(lesson_id):
            lesson = state.load_lesson(lesson_id)
            try:
                restored, revision = undo_revision(lesson)
            except ValueError as exc:
                raise ApiError(409, str(exc)) from exc
            path = state.lessons.path_for(lesson_id)
            stack = _read_redo(path)
            stack.append(revision.model_dump(mode="json", by_alias=True))
            _write_redo(path, stack)
            state.lessons.save(restored)
        return lesson_payload(state, restored)

    @app.post("/api/lessons/{lesson_id}/redo")
    def redo(lesson_id: str) -> dict[str, Any]:
        with state.locked(lesson_id):
            lesson = state.load_lesson(lesson_id)
            path = state.lessons.path_for(lesson_id)
            stack = _read_redo(path)
            if not stack:
                raise ApiError(409, "Nothing to redo.")
            try:
                restored = redo_revision(lesson, Revision.model_validate(stack[-1]))
            except (ValueError, ValidationError) as exc:
                raise ApiError(409, str(exc)) from exc
            _write_redo(path, stack[:-1])
            state.lessons.save(restored)
        return lesson_payload(state, restored)

    # -- pictures and checks

    @app.post("/api/lessons/{lesson_id}/illustrate")
    def illustrate(lesson_id: str, body: IllustrateBody | None = None) -> dict[str, Any]:
        from classdeck.illustrate import illustrate as run_illustrate

        body = body or IllustrateBody()
        endpoint = state.normalise_endpoint(body.endpoint)
        with state.locked(lesson_id):
            lesson = state.load_lesson(lesson_id)
            max_items = max(1, min(body.max, 24))
            provider = state.image_factory(state.settings, endpoint)
            plan = run_illustrate(
                lesson,
                provider=provider,
                image_root=state.image_root,
                max_items=max_items,
                all_items=body.all_items,
                dry_run=True,
            )
            summary: dict[str, Any] = {
                "planned": plan.planned,
                "reused": plan.reused,
                "skipped": plan.skipped,
                "words": plan.words,
                "model": provider.model,
                "estimate_usd": estimate_image_cost_usd(provider.model, plan.planned),
            }
            if body.dry_run:
                return {"report": summary, **lesson_payload(state, lesson)}
            report = run_illustrate(
                lesson,
                provider=provider,
                image_root=state.image_root,
                max_items=max_items,
                all_items=body.all_items,
            )
            state.lessons.save(report.lesson)
        summary.update(generated=report.generated, cost_usd=report.cost_usd)
        return {"report": summary, **lesson_payload(state, report.lesson)}

    @app.post("/api/lessons/{lesson_id}/check")
    def check(lesson_id: str, body: CheckBody | None = None) -> dict[str, Any]:
        from classdeck.pipeline import finish_lesson

        judge = state.provider_for(body.endpoint if body else OFFLINE)
        with state.locked(lesson_id):
            lesson = state.load_lesson(lesson_id)
            curriculum = state.curriculum(lesson.grade, lesson.curriculum.version)
            updated = finish_lesson(lesson, state.sources_of(lesson), curriculum, judge)
            state.lessons.save(updated)
        return lesson_payload(state, updated)

    # -- the page

    static = _static_dir()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            static / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app


# --------------------------------------------------------------------------- serving


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Run the app on the loopback interface until interrupted."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the local app only binds to the loopback interface (127.0.0.1)")
    try:
        import uvicorn
    except ImportError as exc:  # the `api` extra is optional
        raise RuntimeError('uvicorn is missing: pip install -e ".[api]"') from exc
    app = create_app()
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"ClassDeck is running at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
