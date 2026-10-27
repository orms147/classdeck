"""Generate the public ingest fixtures from the unit specs in ``units/``.

Every ``units/<id>.json`` is rendered with ``pagekit`` to ``<id>.pdf``, ``<id>.docx`` and
``<id>.pptx`` next to this script, replacing the files already there. The specs are
self-written imitations of a primary English textbook unit; see ``pagekit.py`` for the format
and ``README.md`` for the policy.

    python eval/fixtures/public/make_fixtures.py

Output is deterministic, so running the script without changing a spec changes no file.
"""

from __future__ import annotations

from pathlib import Path

import pagekit

HERE = Path(__file__).resolve().parent
UNITS = HERE / "units"


def main() -> None:
    for spec in pagekit.load_specs(UNITS):
        for path in pagekit.render_unit(spec, HERE).values():
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
