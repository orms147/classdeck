Prompt templates for the pipeline stages live here, one Markdown file per task.
Name each file `<task>.md` (lowercase, underscores) and load it with `load_prompt("<task>", ...)`; files starting with `_` are test fixtures.
Placeholders use `{{placeholder}}` syntax: `load_prompt` fills each from a keyword argument and raises `KeyError` if one is left unfilled.
