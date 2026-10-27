"""The instructions an agent works from: skills and always-on instruction files.

These are what retro proposes changes to, so the analysis prompts get a list of them
(names and one-line descriptions, not their full text) and pick which one a pattern of
mistakes belongs to.

Skills are `SKILL.md` files with `name` and `description` front matter, in the places
each agent reads them from. Many setups symlink one shared skills directory into several
agents, so skills are de-duplicated by their resolved path. Instruction files are the
always-loaded ones: CLAUDE.md, AGENTS.md and their per-project equivalents.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()

SKILL_DIRS: dict[str, list[Path]] = {
    "claude": [HOME / ".claude" / "skills"],
    "codex": [HOME / ".codex" / "skills"],
    "shared": [HOME / ".agents" / "skills"],
    "cursor": [HOME / ".cursor" / "skills"],
    "opencode": [HOME / ".config" / "opencode" / "skills"],
}
INSTRUCTION_FILES: dict[str, list[Path]] = {
    "claude": [HOME / ".claude" / "CLAUDE.md"],
    "codex": [HOME / ".codex" / "AGENTS.md"],
    "opencode": [HOME / ".config" / "opencode" / "AGENTS.md"],
    "gemini": [HOME / ".gemini" / "GEMINI.md"],
}
PROJECT_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules")
PROJECT_SKILL_DIRS = (".claude/skills", ".agents/skills", ".codex/skills")

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class Instruction:
    kind: str  # "skill" or "file"
    name: str
    description: str
    path: Path
    agents: tuple[str, ...]  # which agents load it

    @property
    def display_path(self) -> str:
        try:
            return "~/" + str(self.path.relative_to(HOME))
        except ValueError:
            return str(self.path)


def inventory(projects: Iterable[Path] = ()) -> list[Instruction]:
    """Every skill and instruction file retro can find, global first, then per project."""
    found: dict[Path, Instruction] = {}

    def add(item: Instruction) -> None:
        key = item.path.resolve()
        if key in found:
            old = found[key]
            agents = tuple(sorted(set(old.agents) | set(item.agents)))
            found[key] = Instruction(old.kind, old.name, old.description, old.path, agents)
        else:
            found[key] = item

    for agent, dirs in SKILL_DIRS.items():
        for root in dirs:
            for skill in _skills_in(root, agent):
                add(skill)
    for agent, files in INSTRUCTION_FILES.items():
        for path in files:
            if path.is_file():
                add(Instruction("file", path.name, _first_line(path), path, (agent,)))
    for project in projects:
        for name in PROJECT_FILES:
            path = project / name
            if path.is_file():
                add(
                    Instruction(
                        "file", f"{project.name}/{name}", _first_line(path), path, ("project",)
                    )
                )
        for rel in PROJECT_SKILL_DIRS:
            for skill in _skills_in(project / rel, "project"):
                add(skill)
    return sorted(found.values(), key=lambda i: (i.kind != "file", i.name))


def _skills_in(root: Path, agent: str) -> list[Instruction]:
    if not root.is_dir():
        return []
    out = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        if any(part.startswith(".") for part in skill_md.relative_to(root).parts):
            continue  # e.g. .trash
        meta = front_matter(skill_md)
        out.append(
            Instruction(
                "skill",
                meta.get("name") or skill_md.parent.name,
                meta.get("description", ""),
                skill_md,
                (agent,),
            )
        )
    return out


def front_matter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def _first_line(path: Path) -> str:
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().lstrip("#").strip()
            if line:
                return line[:120]
    except (OSError, UnicodeDecodeError):
        pass
    return ""
