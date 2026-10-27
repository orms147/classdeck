# Local app (`classdeck serve`)

A small web app that runs on the teacher's own computer. Everything the CLI does, without a terminal.

```
classdeck serve            # http://127.0.0.1:8765, opens the browser
classdeck serve --port 9000 --no-open
```

The server binds to `127.0.0.1` only. Lessons, sources and exports live in the same work directory as the CLI (`CLASSDECK_WORKDIR`, default `out/`), so the two can be mixed. The page is plain HTML, CSS and JavaScript served by the app itself — no build step, no CDN, works offline (a model endpoint is only needed for drafting and editing with a model).

## Screens

1. **Lessons** — the lessons in the work directory (title, grade, unit, date, warnings count), newest first; *New lesson* button.
2. **New lesson** — drop a PDF / DOCX / PPTX (or pick one of the bundled sample units); grade 3–5; unit (list from the curriculum, with titles); lesson 1–3; period 1–2; model: *Meta (Muse Spark)*, *OpenRouter*, *DeepSeek* or *Offline (no model)* — only endpoints whose key is configured are enabled, keys are never shown; *draw flashcard pictures* (with the price estimate); theme. *Create* shows the five stages live (ingest, outline, draft, pictures, export) and then opens the lesson.
3. **Lesson** — three panes:
   - left: the blocks grouped by phase (kind icon, provenance badge *Source / Derived / AI-added*, warning dot);
   - centre: the deck preview (theme switcher above it; *Present* opens it full screen in a new tab);
   - right: the selected block — readable content, its source quotes with page numbers, its checks; an instruction box with the preset buttons (*Make it a pair-work activity*, *Simpler instructions for pupils*, …) and free text. *Propose* shows before/after, affected blocks and the checks on the proposal; *Accept* or *Discard*. *Undo* / *Redo* in the header.
   - header: *Download* HTML / PPTX / DOCX, *Draw pictures*, *Re-check*.

The UI is in Vietnamese by default with an English switch (strings in one file, `app/static/i18n.js`).

## API (JSON, same origin)

| method | path | does |
|---|---|---|
| GET | `/api/config` | endpoints (name, label, configured: bool, default model), themes, grades with units (id, title, lessons), samples |
| GET | `/api/lessons` | list |
| POST | `/api/jobs` | multipart: file or `sample`, grade, unit, lesson, period, endpoint, pictures, theme → `{job_id}`; runs the pipeline in a background thread |
| GET | `/api/jobs/{id}` | `{stage, stages_done, status: running/done/error, lesson_id?, error?}` |
| GET | `/api/lessons/{id}` | lesson JSON plus a per-block summary for the panes |
| GET | `/api/lessons/{id}/deck?theme=` | the rendered deck HTML (for the iframe) |
| GET | `/api/lessons/{id}/export/{html,pptx,docx}` | download |
| POST | `/api/lessons/{id}/proposals` | `{block_ids, instruction, endpoint}` → proposal (before/after per block, affected, checks) kept in memory under an id |
| POST | `/api/lessons/{id}/proposals/{pid}/accept` | apply, save, return the lesson |
| POST | `/api/lessons/{id}/undo`, `/redo` | as the CLI |
| POST | `/api/lessons/{id}/illustrate` | draw missing pictures (`max`, `endpoint`) |
| POST | `/api/lessons/{id}/check` | re-run grounding and level checks |

Uploads are limited to 30 MB and to the three formats; file names are never used as paths. Model and network errors come back as `{error}` with HTTP 502 and are shown in the page; nothing is half-saved.

## Module layout

```
src/classdeck/app/
  server.py      FastAPI app factory create_app(settings), routes, job runner (threads)
  jobs.py        in-memory job and proposal registries
  static/        index.html, app.js, app.css, i18n.js
```

Requires the `api` extra: `pip install -e ".[api]"`.
