"""Scanned pages: read a page that is only a picture with a vision-capable chat model.

A scanned textbook page has no text layer and one picture covering the page. The page image is
sent to the model with the ``read_page`` prompt; the reply (a ``PageLayout``: blocks in reading
order with text and page-relative boxes) becomes spans exactly like the spans of a text PDF.
Replies are cached by page-image hash through the ``CachingProvider`` the caller passes in.
See ``docs/secondary-units.md`` (Scanned pages).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from classdeck.ingest.base import ImageSink, PageBuilder, normalize_text
from classdeck.lesson import Page, SpanKind
from classdeck.llm import (
    ImagePart,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    StructuredOutputError,
    TextPart,
    estimate_cost_usd,
    extract_json,
    load_prompt,
)

logger = logging.getLogger(__name__)

SCAN_COVERAGE = 0.80
"""Share of the page one picture must cover for the page to count as scanned."""
READ_DPI = 150
READ_MAX_SIDE_PX = 1600
CROP_DPI = 200
MIN_CROP_SIDE_PT = 24.0
MAX_REPLY_TOKENS = 12000
READ_ATTEMPTS = 3
RETRY_DELAY_S = 5.0
"""A page whose read failed (after the provider's own retries, which last a few seconds) is
asked for again after a pause: a network that drops for half a minute must not leave pages as
pictures."""
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403, 404})

type _BBox = tuple[float, float, float, float]

_KIND_ALIASES = {
    "title": "heading",
    "subheading": "heading",
    "text": "paragraph",
    "dialogue": "speaker_line",
    "dialogue_line": "speaker_line",
    "speaker": "speaker_line",
    "image": "picture",
    "photo": "picture",
    "illustration": "picture",
    "exercise": "instruction",
    "table": "table_row",
}
_KEY_WORD = re.compile(r"[A-Za-z_]+")
_BARE_NUMBER = re.compile(r"^(\d{1,2})\s+(?=\S)")


class Block(BaseModel):
    """One block of a scanned page, as the model reports it."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal[
        "section",
        "heading",
        "instruction",
        "paragraph",
        "list_item",
        "table_row",
        "caption",
        "picture",
        "speaker_line",
    ]
    text: str = ""
    bbox: list[float] | None = None
    speaker: str | None = None
    column: int | None = None
    highlight: list[str] = []
    track: int | None = None

    @field_validator("highlight", mode="before")
    @classmethod
    def _phrases(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str | int | float)]
        return []

    @field_validator("track", mode="before")
    @classmethod
    def _track_number(cls, value: object) -> object:
        """``3``, ``"3"`` and ``"Track 3"`` all mean track 3; anything else is no track."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return int(value) if 0 < value < 1000 else None
        found = re.search(r"\d{1,3}", value) if isinstance(value, str) else None
        return int(found.group()) if found else None

    @model_validator(mode="before")
    @classmethod
    def _plain_keys(cls, data: Any) -> Any:
        """Some models echo the prompt's description into a key ("bbox [x0, y0, x1, y1]")."""
        if not isinstance(data, dict):
            return data
        keys: dict[str, Any] = {}
        for key, value in data.items():
            match = _KEY_WORD.match(key) if isinstance(key, str) else None
            keys.setdefault(match.group(0).lower() if match else key, value)
        return keys

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower().replace(" ", "_").replace("-", "_")
            return _KIND_ALIASES.get(lowered, lowered)
        return value

    @field_validator("text", mode="before")
    @classmethod
    def _text_or_empty(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("bbox", mode="after")
    @classmethod
    def _page_relative(cls, value: list[float] | None) -> list[float] | None:
        """Keep a usable box (clamped to the page) or ``None``; never reject the block."""
        if value is None or len(value) != 4:
            return None
        coordinates = list(value)
        if max(coordinates) > 1.5:  # some models answer on a 0-1000 grid
            coordinates = [coordinate / 1000 for coordinate in coordinates]
        x0, y0, x1, y1 = (min(max(coordinate, 0.0), 1.0) for coordinate in coordinates)
        if x1 <= x0 or y1 <= y0:
            return None
        return [x0, y0, x1, y1]


class PageLayout(BaseModel):
    """The model's reading of one page: blocks in reading order."""

    model_config = ConfigDict(extra="ignore")

    blocks: list[Block]

    @model_validator(mode="before")
    @classmethod
    def _bare_list(cls, data: Any) -> Any:
        return {"blocks": data} if isinstance(data, list) else data


@dataclass(frozen=True)
class ScanReport:
    """What reading one scanned page cost."""

    page: int
    ok: bool
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    cost_usd: float | None = None
    error: str = ""


# --- detection and rendering -------------------------------------------------------------------


def is_scanned_page(page: pymupdf.Page) -> bool:
    """True when the page has no text and one picture covers at least 80% of it."""
    if page.get_text("text").strip():
        return False
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    for xref in dict.fromkeys(image[0] for image in page.get_images(full=True)):
        try:
            rects = page.get_image_rects(xref)
        except Exception as error:
            logger.debug("page %s: image xref %s skipped: %s", page.number + 1, xref, error)
            continue
        for rect in rects:
            visible = rect & page.rect
            if not visible.is_empty and visible.width * visible.height >= SCAN_COVERAGE * area:
                return True
    return False


def render_page_png(page: pymupdf.Page) -> bytes:
    """The page as a PNG at about 150 dpi, at most 1600 px on the long side."""
    scale = min(READ_DPI / 72, READ_MAX_SIDE_PX / max(page.rect.width, page.rect.height))
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png")


# --- the model call ----------------------------------------------------------------------------


class _Recording:
    """Forwards to a provider and reports every response, so cost is known even after a retry."""

    def __init__(self, inner: LLMProvider, on_response: Callable[[LLMResponse], None]) -> None:
        self._inner = inner
        self._on_response = on_response
        self.name = inner.name
        self.model = inner.model

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        response = self._inner.complete(messages, **kwargs)
        self._on_response(response)
        return response


def read_page(
    png_bytes: bytes,
    provider: LLMProvider,
    *,
    page_number: int,
    on_response: Callable[[LLMResponse], None] | None = None,
) -> PageLayout:
    """Ask a vision model for the reading order of one scanned page.

    ``provider`` should be the caching wrapper: the request, and so the cache key, contains the
    page image, which makes the cache a cache by image hash. One retry after an unparseable
    reply; if that fails too, ``StructuredOutputError`` (an ``LLMError``) is raised.
    """
    url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    prompt = load_prompt("read_page", page_number=str(page_number))
    messages = [
        Message(
            role="user",
            content=[TextPart(text=prompt), ImagePart(url=url, detail="high")],
        )
    ]
    if on_response is not None:
        provider = _Recording(provider, on_response)
    # The shape is described in the prompt, not passed as the endpoint's ``response_format``:
    # constrained decoding on the Meta endpoint garbled the ``bbox`` key of long replies.
    attempts: list[str] = []
    error = ""
    for attempt in range(2):
        response = provider.complete(messages, temperature=0.0, max_tokens=MAX_REPLY_TOKENS)
        attempts.append(response.text)
        try:
            return PageLayout.model_validate(json.loads(extract_json(response.text)))
        except json.JSONDecodeError as exc:
            error = f"The reply is not valid JSON: {exc}"
        except ValidationError as exc:
            error = f"The JSON does not match the schema: {exc.errors(include_url=False)[:5]}"
        if attempt == 0:
            messages = [
                *messages,
                Message(role="assistant", content=response.text[:2000]),
                Message(
                    role="user",
                    content=f"{error}\nAnswer again with the single JSON object only, "
                    "keys spelled exactly as in the example.",
                ),
            ]
    raise StructuredOutputError(
        f"No valid PageLayout after 2 attempts. Last error: {error}", attempts
    )


# --- spans -------------------------------------------------------------------------------------


def extract_scanned_page(
    page: pymupdf.Page,
    number: int,
    sink: ImageSink,
    provider: LLMProvider | None,
    report: Callable[[ScanReport], None] | None = None,
) -> Page:
    """Spans of one scanned page: read by ``provider``, or the page picture alone without one."""
    if provider is None:
        logger.warning(
            "page %s is a scan and no model endpoint is configured: kept as one picture, "
            "its text is not read",
            number,
        )
        if report is not None:
            report(ScanReport(page=number, ok=False, error="no model endpoint is configured"))
        return _picture_only(page, number, sink)

    responses: list[LLMResponse] = []
    png = render_page_png(page)
    error: LLMError | None = None
    for attempt in range(1, READ_ATTEMPTS + 1):
        try:
            layout = read_page(png, provider, page_number=number, on_response=responses.append)
        except LLMError as exc:
            error = exc
            logger.warning(
                "page %s: reading the scan failed (attempt %s/%s): %s",
                number,
                attempt,
                READ_ATTEMPTS,
                exc,
            )
            if exc.status in _NO_RETRY_STATUS:  # a rejected key does not get better
                break
            if attempt < READ_ATTEMPTS:
                time.sleep(RETRY_DELAY_S * attempt)
            continue
        if report is not None:
            report(_report(number, responses, ok=True))
        return _layout_page(page, number, layout, sink)
    logger.warning("page %s could not be read; kept as one picture", number)
    if report is not None:
        report(_report(number, responses, ok=False, error=str(error)))
    return _picture_only(page, number, sink)


def _picture_only(page: pymupdf.Page, number: int, sink: ImageSink) -> Page:
    builder = PageBuilder(number)
    bbox = _bbox(page.rect)
    builder.add(SpanKind.IMAGE, "", bbox=bbox, image_path=_save_crop(page, bbox, sink, number, 1))
    return builder.build()


def _layout_page(page: pymupdf.Page, number: int, layout: PageLayout, sink: ImageSink) -> Page:
    builder = PageBuilder(number)
    rows: list[Block] = []  # consecutive table rows form one table span, like a text PDF

    def flush_rows() -> None:
        if not rows:
            return
        text = "\n".join(line for row in rows if (line := _clean(row.text, row.kind)))
        builder.add(SpanKind.TABLE, text, bbox=_union([_to_points(row, page) for row in rows]))
        rows.clear()

    for block in layout.blocks:
        if block.kind == "table_row":
            rows.append(block)
            continue
        flush_rows()
        bbox = _to_points(block, page)
        if block.kind == "picture":
            if bbox is None or not _big_enough(bbox):
                logger.debug("page %s: picture block without a usable box skipped", number)
                continue
            path = _save_crop(page, bbox, sink, number, builder.image_count + 1)
            # The model's short neutral description is kept as the span text: it is the alt text
            # of the picture and tells the drafter what the picture shows.
            builder.add(SpanKind.IMAGE, normalize_text(block.text), bbox=bbox, image_path=path)
            continue
        text = _block_text(block)
        if text:
            builder.add(
                _SPAN_KINDS[block.kind],
                text,
                bbox=bbox,
                highlights=_highlights(block, text),
                track=block.track if block.kind in ("instruction", "heading") else None,
            )
    flush_rows()
    return builder.build()


_SPAN_KINDS: dict[str, SpanKind] = {
    "section": SpanKind.HEADING,
    "heading": SpanKind.HEADING,
    "instruction": SpanKind.INSTRUCTION,
    "list_item": SpanKind.LIST_ITEM,
    "speaker_line": SpanKind.PARAGRAPH,
    "paragraph": SpanKind.PARAGRAPH,
    "caption": SpanKind.CAPTION,
}


def _clean(text: str, kind: str) -> str:
    text = normalize_text(text)
    if kind in ("instruction", "list_item", "table_row"):
        text = _BARE_NUMBER.sub(r"\1. ", text)
    return text


def _highlights(block: Block, text: str) -> list[str]:
    """The highlighted phrases the model reported that really occur in the block's text."""
    found: list[str] = []
    lowered = text.lower()
    for phrase in block.highlight:
        clean = normalize_text(phrase).strip().rstrip(".,;:!?").strip()
        if clean and clean.lower() in lowered and clean not in found:
            found.append(clean)
    return found


def _block_text(block: Block) -> str:
    text = _clean(block.text, block.kind)
    if block.kind != "speaker_line":
        return text
    speaker = normalize_text(block.speaker or "").rstrip(":").strip()
    if not speaker or text.lower().startswith(f"{speaker.lower()}:"):
        return text
    return f"{speaker}: {text}" if text else ""


def _to_points(block: Block, page: pymupdf.Page) -> _BBox | None:
    if block.bbox is None:
        return None
    x0, y0, x1, y1 = block.bbox
    rect = page.rect
    return (
        rect.x0 + x0 * rect.width,
        rect.y0 + y0 * rect.height,
        rect.x0 + x1 * rect.width,
        rect.y0 + y1 * rect.height,
    )


def _union(boxes: list[_BBox | None]) -> _BBox | None:
    known = [box for box in boxes if box is not None]
    if not known:
        return None
    return (
        min(box[0] for box in known),
        min(box[1] for box in known),
        max(box[2] for box in known),
        max(box[3] for box in known),
    )


def _big_enough(bbox: _BBox) -> bool:
    return bbox[2] - bbox[0] >= MIN_CROP_SIDE_PT and bbox[3] - bbox[1] >= MIN_CROP_SIDE_PT


def _save_crop(
    page: pymupdf.Page, bbox: _BBox, sink: ImageSink, number: int, index: int
) -> str | None:
    if not sink.enabled:
        return None
    try:
        blob = page.get_pixmap(clip=pymupdf.Rect(bbox), dpi=CROP_DPI).tobytes("png")
    except Exception as error:
        logger.debug("page %s picture %s: render failed: %s", number, index, error)
        return None
    return sink.save(blob, page=number, index=index)


def _bbox(rect: pymupdf.Rect) -> _BBox:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _report(number: int, responses: list[LLMResponse], *, ok: bool, error: str = "") -> ScanReport:
    paid = [response for response in responses if not response.cached]
    cost = None
    for response in paid:
        part = estimate_cost_usd(response.model, response.usage)
        if part is not None:
            cost = (cost or 0.0) + part
    if not paid and responses:
        cost = 0.0
    return ScanReport(
        page=number,
        ok=ok,
        model=responses[-1].model if responses else "",
        input_tokens=sum(r.usage.input_tokens or 0 for r in paid),
        output_tokens=sum(r.usage.output_tokens or 0 for r in paid),
        cached=bool(responses) and not paid,
        cost_usd=cost,
        error=error,
    )
