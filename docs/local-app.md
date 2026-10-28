# Local app (`classdeck serve`)

A small web app that runs on the teacher's own computer. Everything the CLI does, without a terminal.

```
classdeck serve            # http://127.0.0.1:8765, opens the browser
classdeck serve --port 9000 --no-open
```

The server binds to `127.0.0.1` only. Lessons, sources and exports live in the same work directory as the CLI (`CLASSDECK_WORKDIR`, default `out/`), so the two can be mixed. The page is plain HTML, CSS and JavaScript served by the app itself — no build step, no CDN, works offline (a model endpoint is only needed for drafting and editing with a model).

## Teacher-first simplification (2026-10-29)

The app is meant to become a desktop icon on the teacher's computer: open it and use it. So:

- **No internal ids on screen.** Teachers see *Grade 9 · Lesson 1*, *Unit 1*, never `ta9.u1`, `l_45d981`, block ids or provenance jargon (block ids appear only in the teacher-mode footer of the deck). Deck covers and titles read `Grade <g> · Lesson <n>` (and `Period <p>` only for primary units that have two periods).
- **New lesson in one card:** the file (drop / pick / sample), **Grade** 1–9 (segmented buttons), **Unit** a number input (1–20), **Lesson** a number (1–7 for grades 6–9; 1–3 plus Period 1–2 for grades 1–5), the theme preview chips (sunny selected), and one big *Create lesson* button. Everything else (model, pictures, reference plan) sits under *More options*, collapsed.
- **Grades without curriculum data** (1, 2, 6, 7, 8 today, or any unit number not in the YAML): the curriculum unit is built on the fly from the ingested pages — the unit title from the first heading, outcomes from the "This unit includes" box when present (secondary) or from the lesson's vocabulary and patterns (primary), no closed word list (the words-from-unit check applies). Grades 1–5 use the primary path, 6–9 the secondary path. Nothing to configure.
- **Settings screen** (gear icon): *OpenRouter API key* (password field with show/hide, *Test* button that sends one tiny request and shows the model's reply or the error), *Model* (text field, default `openai/gpt-6-luna`; any OpenRouter model id can be typed, e.g. a newer model later; *Test* checks it), *Default theme*, *Work folder* (read-only path with *Open folder*). Saved to the user settings file. Keys are shown masked after saving and never sent anywhere except the model endpoint.
- **User settings file:** `%APPDATA%\ClassDeck\settings.json` on Windows, `~/.config/classdeck/settings.json` elsewhere (`CLASSDECK_HOME` overrides). Precedence: explicit environment variables / `.env` (developers) → user settings file → built-in defaults. The default endpoint is `openrouter` with model `openai/gpt-6-luna`; Meta and DeepSeek profiles stay available for developers.
- **No key yet:** the *Create* button still works with the offline template pipeline, and a friendly banner links to Settings ("Add your OpenRouter key for better lessons").

## Screens

There is no side bar: a slim top bar holds the logo, *Lessons*, the VI / EN switch and the gear (Settings).

1. **Lessons** — the lessons in the work directory, newest first. Each card reads *Grade 3 · Lesson 1 · Period 1* with *Unit 1 · Hello* under it, a warnings chip only when there are warnings, and the date; *New lesson* button; the no-key banner when no key is saved.
2. **New lesson** — the one card described above: file or sample, grade 1–9, unit number, lesson (and period), theme chips, *Create lesson*; *More options* (collapsed) holds the model override, flashcard pictures (with the price estimate) and, for grades 6–9, the reference plan. The model is the configured one when a key exists, the offline template pipeline otherwise. *Create* shows the five stages live (ingest, outline, draft, pictures, export) and then opens the lesson.
3. **Settings** — the screen above.
4. **Lesson** — three panes:
   - left: the blocks grouped by phase (kind icon, provenance badge *Source / Derived / AI-added*, warning dot);
   - centre: the deck preview (theme switcher above it; *Present* opens it full screen in a new tab);
   - right: the selected block — readable content, its source quotes with page numbers, its checks; an instruction box with the preset buttons (*Make it a pair-work activity*, *Simpler instructions for pupils*, …) and free text. *Propose* shows before/after, affected blocks and the checks on the proposal; *Accept* or *Discard*. *Undo* / *Redo* in the header.
   - header: a back arrow, *Grade 3 · Lesson 1 · Period 1* with *Unit 1 · Hello*, *Undo* / *Redo* as icon buttons, a *More* menu (*Re-check*, *Draw pictures*) and *Download*. Downloads are named like `Grade-3-Lesson-1-Period-1.html`.

The UI is in Vietnamese by default with an English switch (strings in one file, `app/static/i18n.js`).

## API (JSON, same origin)

| method | path | does |
|---|---|---|
| GET | `/api/config` | endpoints (name, label, host, configured: bool, default model), `default_endpoint`, `key_set`, `model`, themes, `default_theme`, grades 1–9 (`unit_shape`, `lessons`, `periods`, units listed in the curriculum file), `max_unit`, samples |
| GET | `/api/settings` | `{key_set, key_hint, key_source, model, default_model, default_theme, themes, workdir, offline_mode}`; the key itself is never returned, only a masked hint such as `sk-…abcd` |
| PUT | `/api/settings` | `{api_key?, model?, default_theme?}`: a missing field stays, `""` removes it; saved to the user settings file and used at once; returns the same as GET |
| POST | `/api/settings/test` | `{api_key?, model?}` (the saved ones when missing): one tiny request; `{ok, model, reply}` or `{ok: false, code?, error}` (the key is masked out of the error) |
| POST | `/api/open-folder` | opens the work folder in the file manager (`os.startfile` on Windows); loopback clients only |
| GET | `/api/lessons` | list: `title` (*Grade 3 · Lesson 1 · Period 1*), `unit` (*Unit 1 · Hello*), `unit_no`, `unit_title`, grade, date, warnings (`unit_id` is kept for tools) |
| POST | `/api/jobs` | multipart: file or `sample`, optional `reference` (.docx, grades 6–9), `grade` 1–9, `unit` (a number 1–20; an id such as `ta3.u1` is accepted), lesson, period, endpoint, `model` (override), pictures, theme → `{job_id}`; runs the pipeline in a background thread. The curriculum unit comes from the grade's file when it lists it, else it is built from the ingested pages |
| GET | `/api/jobs/{id}` | `{stage, stages_done, status: running/done/error, lesson_id?, error?}` |
| GET | `/api/lessons/{id}` | lesson JSON (its `title` is the friendly label) plus a per-block summary for the panes (`summary.label`, `summary.unit_line`, blocks, sections) |
| GET | `/api/lessons/{id}/deck?theme=` | the rendered deck HTML (for the iframe) |
| GET | `/api/lessons/{id}/export/{html,pptx,docx}` | download |
| POST | `/api/lessons/{id}/proposals` | `{block_ids, instruction, endpoint}` → proposal (before/after per block, affected, checks) kept in memory under an id |
| POST | `/api/lessons/{id}/proposals/{pid}/accept` | apply, save, return the lesson |
| POST | `/api/lessons/{id}/edits` | `{edits: [{block_id, field, value}], theme?}` from the pencil and Save on the deck: one teacher direct edit (one revision, so Undo works), re-check, save, re-export `lesson.html`; returns the lesson payload plus `notices` (provenance changes, new warnings), `changed` and `exported`. A refused edit is `422 {error, block_id, field}`. The deck in the middle pane posts it itself and hands the fresh payload to the page (`classdeck:saved`), which reloads the deck on the same slide |
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

## Desktop app (Windows)

Teachers install ClassDeck like any other program and open it from an icon; they never see a terminal, Python or a port number.

**What the teacher does.** Double-click `ClassDeck-Setup.exe` → it copies the program to `%LOCALAPPDATA%\Programs\ClassDeck` (no administrator rights) and puts a *ClassDeck* icon on the desktop and in the Start menu, then starts it. The icon opens the app in its own window; closing the window stops it. The first screen asks for the OpenRouter key once (Settings, as before). Uninstall: *Uninstall ClassDeck* in the Start menu folder (removes the program, keeps the lessons).

**Where things live.**

| what | where |
|---|---|
| program | `%LOCALAPPDATA%\Programs\ClassDeck` |
| lessons, uploaded pages, exported decks | `Documents\ClassDeck` (the work folder; the teacher can open and back it up) |
| settings (API key, model) | `%APPDATA%\ClassDeck\settings.json` (unchanged) |
| curriculum files | `%APPDATA%\ClassDeck\curriculum`, copied from the program on first start (units built from uploaded pages are added there, so it must be writable) |
| model response cache | `%LOCALAPPDATA%\ClassDeck\cache` |

**How it runs** (`classdeck/app/desktop.py`, entry `classdeck-desktop`): set the folders above unless the environment already names them, find a free port from 8765 on 127.0.0.1, start the server in a background thread, open the app in a native window (pywebview on the system WebView2; *Present* opens the deck in the default browser for full-screen projection; downloads are allowed). The built app always has pywebview; run from source without the `desktop` extra, `classdeck-desktop` opens the default browser instead and runs until Ctrl+C. A second start while one is running just opens a new window on the running server.

**Building** (developer, Windows): `packaging/windows/build.ps1` makes the icon (`make_icon.py`, Pillow), runs PyInstaller with `classdeck.spec` (one folder, windowed, the icon, the package data: static files, themes, prompts, word lists, and the `curriculum/` tree), zips `dist/ClassDeck`, and wraps the zip and `install.ps1` into `dist/ClassDeck-Setup.exe` with the Windows IExpress tool. The setup is not code-signed, so Windows SmartScreen shows *Windows protected your PC* on first run (*More info → Run anyway*). Build-only requirements: the `desktop` extra (`pywebview`) and `pyinstaller`.
