# orc repo

This repo is the orc tool itself: `bin/orc` (a stdlib-only Python 3.11 CLI) plus the `skill/orchestrate` Claude Code skill.

- Run the tests after every change: `python3 -m unittest discover tests`. They use `tests/fake_codex.py`; no real Codex calls.
- Keep `bin/orc` dependency-free and in one file.
- Keep `skill/orchestrate/SKILL.md` short. It is loaded into the lead's context, so every line costs tokens on every orchestrated task. Put details in `reference/`.
- Output that the lead reads (the `orc run` summary, `orc check`, `orc report`) must stay compact. Don't add verbose logging to stdout; progress goes to stderr.
- Research notes live in `docs/research/`; update `docs/design.md` when behavior changes.
