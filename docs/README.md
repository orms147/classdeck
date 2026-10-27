# ClassDeck — design documents

Read in this order if you are new:

1. [architecture.md](architecture.md) — the pipeline, the modules, the boundaries between them.
2. [lesson-model.md](lesson-model.md) — the data everything else operates on: blocks, citations, provenance, dependency graph, revisions.
3. [source-grounding.md](source-grounding.md) — how spans are addressed, how citations are verified, how AI-added content is labelled, and the metric that comes out of it.
4. [planning.md](planning.md) — outliner and drafter: deterministic skeleton from the textbook's activity structure, model refinement, template drafting for offline runs.
5. [editing.md](editing.md) — the select → instruct → preview → accept/undo loop and how dependent blocks are handled.
6. [english-rules.md](english-rules.md) — lesson structure for primary English, block kinds, prompt rules, and the deterministic level checks.
7. [output-formats.md](output-formats.md) — the HTML deck (primary, designed for pupils), PPTX, DOCX, and prompt packs for teachers without an API key.
8. [curriculum-config.md](curriculum-config.md) — versioned YAML for curriculum outcomes, word lists and grammar scope.
9. [llm-provider.md](llm-provider.md) — one provider interface for any OpenAI-compatible API, the mock provider, caching, structured output, the judge role.
10. [evaluation.md](evaluation.md) — eval cases, metrics, LLM judge, fixture policy.
11. [roadmap.md](roadmap.md) — phases and what "done" means for each.

Conventions used in these documents:

- **Span** — the smallest addressable unit of a source document (a paragraph, an activity instruction, a caption, a picture), identified by `doc_id`, `page`, `span_id`.
- **Block** — the smallest editable unit of a lesson, identified by `block_id`, with its own citations and provenance.
- **Outcome** — a curriculum "yêu cầu cần đạt" with a stable ID, e.g. `ta3.u1.o1`.
- Vietnamese terms are kept where they are the proper name of something (*Chương trình GDPT 2018*, *Global Success*).
- Textbook excerpts in examples are invented stand-ins with the same shape as the real thing, not copies.
