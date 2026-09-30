#!/usr/bin/env python3
"""Package each skill in plugin/skills/ as dist/<name>.skill, a zip for claude.ai upload or manual install.

The archive holds the skill folder (`<name>/SKILL.md`, ...). Output is deterministic, so
`--check` can verify that dist/ matches the sources (the tests run it).
"""
import io
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "plugin" / "skills"
DIST = ROOT / "dist"
# Frontmatter keys accepted by claude.ai uploads and the Skills API (Agent Skills spec).
ALLOWED = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def frontmatter_keys(skill_md: Path) -> set[str]:
    m = re.match(r"^---\n(.*?)\n---\n", skill_md.read_text(), re.S)
    if not m:
        sys.exit(f"{skill_md}: missing frontmatter")
    return {line.split(":", 1)[0].strip() for line in m.group(1).splitlines() if line and not line.startswith((" ", "\t"))}


def build(skill: Path) -> bytes:
    bad = frontmatter_keys(skill / "SKILL.md") - ALLOWED
    if bad:
        sys.exit(f"{skill.name}: frontmatter keys not allowed outside Claude Code: {sorted(bad)}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in skill.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            info = zipfile.ZipInfo(f"{skill.name}/{f.relative_to(skill).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if f.stat().st_mode & 0o111 else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
    return buf.getvalue()


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    DIST.mkdir(exist_ok=True)
    for skill in sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").exists()):
        out, data = DIST / f"{skill.name}.skill", build(skill)
        if check:
            if not out.exists() or out.read_bytes() != data:
                stale.append(out.name)
        else:
            out.write_bytes(data)
            print(f"wrote {out.relative_to(ROOT)} ({len(data) // 1024} KB)")
    if stale:
        print(f"dist is out of date: {', '.join(stale)}. Run: python3 tools/build_skills.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
