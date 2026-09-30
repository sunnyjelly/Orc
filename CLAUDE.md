# orc repo

This repo is the orc Claude Code plugin and its marketplace. The plugin lives in `plugin/`: the `orchestrate` skill, which bundles the stdlib-only Python 3.11 CLI at `skills/orchestrate/scripts/orc`, and the portable `delegate` skill.

- Run the tests after every change: `python3 -m unittest discover tests`. They use `tests/fake_codex.py`; no real Codex calls.
- Keep `scripts/orc` dependency-free and in one file.
- After changing anything under `plugin/skills/`, run `python3 tools/build_skills.py` to rebuild `dist/*.skill` (a test fails if they are stale). Bump `version` in `plugin/.claude-plugin/plugin.json` for releases.
- Skill frontmatter may only use `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`, so the `.skill` files upload to claude.ai.
- Keep `plugin/skills/*/SKILL.md` short. It is loaded into the lead's context, so every line costs tokens on every orchestrated task. Put details in `reference/`.
- Output that the lead reads (the `orc run` summary, `orc check`, `orc report`) must stay compact. Don't add verbose logging to stdout; progress goes to stderr.
- Research notes live in `docs/research/`; update `docs/design.md` when behavior changes.
