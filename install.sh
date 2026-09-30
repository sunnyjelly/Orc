#!/usr/bin/env bash
# Install orc for the current user: CLI on PATH, skill in ~/.claude/skills.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bin_dir="${ORC_BIN_DIR:-$HOME/.local/bin}"
skill_dir="$HOME/.claude/skills/orchestrate"

mkdir -p "$bin_dir" "$(dirname "$skill_dir")"
ln -sf "$here/bin/orc" "$bin_dir/orc"
if [ -e "$skill_dir" ] && [ ! -L "$skill_dir" ]; then
  echo "refusing to replace existing $skill_dir (not a symlink)"; exit 1
fi
ln -sfn "$here/skill/orchestrate" "$skill_dir"

echo "Installed: $bin_dir/orc -> $here/bin/orc"
echo "Installed: $skill_dir -> $here/skill/orchestrate"
case ":$PATH:" in *":$bin_dir:"*) ;; *) echo "Add $bin_dir to your PATH." ;; esac
cat <<'EOF'

Next:
  1. npm i -g @openai/codex && codex login     # sign in with your ChatGPT account
  2. Add to ~/.claude/settings.json:
     {
       "statusLine": {"type": "command", "command": "orc statusline", "refreshInterval": 30}
     }
  3. orc doctor
EOF
