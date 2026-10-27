# eval/

End-to-end evaluation of the pipeline. See [docs/evaluation.md](../docs/evaluation.md) for metrics and case format.

```
cases/              one YAML per case
fixtures/public/    material we may publish (self-written mini-chapters); used in CI with the mock provider
fixtures/private/   real textbook pages and lesson plans — git-ignored, never committed
results/            run outputs — git-ignored
compare.py          prints the metric diff between two results files
```

## Run

Each case ingests its source, outlines and drafts the period, verifies citations, runs the English validators, applies the scripted edits and computes the metrics. `source` in a case file is relative to this directory; a case whose fixture is missing is reported as `skipped` instead of failing.

```bash
classdeck eval eval/cases/en3-u1-l1-p1-public.yaml           # one case, configured provider
classdeck eval eval/cases --provider mock                    # all cases, offline
classdeck eval eval/cases --report eval/results/2026-10-30.json
python eval/compare.py eval/results/before.json eval/results/after.json
```

The same thing from Python:

```python
from datetime import UTC, datetime
from pathlib import Path

from classdeck.config import Settings
from classdeck.evaluation import load_cases, run_cases
from classdeck.llm import build_provider

settings = Settings()
report = run_cases(
    load_cases(Path("eval/cases")),
    settings=settings,
    provider=build_provider(settings, "generator"),
    judge_provider=build_provider(settings, "judge"),
    eval_dir=Path("eval"),
    workdir=Path("out/eval"),
    timestamp=datetime.now(UTC),
)
print(report.table())
report.write(Path("eval/results/run.json"))
```

## What runs offline

With the mock provider the pipeline uses the template outline and drafter, so every automatic metric that comes from code (grounding, vocabulary, grammar, skill and outcome coverage, timing) is deterministic. Two things need a real model and are **skipped, not simulated**: the scripted edits (`edit_locality` and `edit_success` stay empty, with the reason in the result) and the LLM judge (`judge_source: skipped`).

The judge uses the independent `JUDGE_*` endpoint when it is configured; otherwise the generator grades its own output and the scores are marked `judge_source: generator`.

## Results file

`EvalReport.to_json()` is a list of per-case results (metrics, expectation pass/fail, edit outcomes, notes) plus a summary row of means and counts. `compare` prints, for every case in either file, the change of each metric, then the change of the summary.
