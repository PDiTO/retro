from __future__ import annotations

import json
import subprocess
from pathlib import Path

from retro.audit import FIXTURE_MARKER, audit
from tests.conftest import ROOT, SOMEONE_HOME


def rules(root: Path) -> list[tuple[str, str]]:
    return [(f.path.name, f.rule) for f in audit(root)]


def test_this_repo_is_clean() -> None:
    assert audit(ROOT) == []


def test_flags_transcripts_unless_marked_synthetic(tmp_path: Path) -> None:
    record = {"sessionId": "s", "uuid": "u", "message": {"role": "user", "content": "hi"}}
    (tmp_path / "real.jsonl").write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    (tmp_path / "fake.jsonl").write_text(
        json.dumps(FIXTURE_MARKER) + "\n" + json.dumps(record) + "\n"
    )
    codex = {"type": "response_item", "payload": {"type": "message"}}
    (tmp_path / "rollout.json").write_text(json.dumps(codex))
    assert rules(tmp_path) == [
        ("real.jsonl", "transcript-record"),
        ("rollout.json", "transcript-record"),
    ]


def test_flags_secrets_and_home_paths(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text(
        f"see /home/dev/src (fine)\nsee {SOMEONE_HOME}/src\ntoken " + "gh" + "p_" + "x" * 36 + "\n"
    )
    findings = audit(tmp_path)
    assert [(f.line, f.rule) for f in findings] == [(2, "home-path"), (3, "github-token")]


def test_skips_binary_files(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    assert audit(tmp_path) == []


def test_respects_gitignore(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("private/\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "notes.md").write_text(SOMEONE_HOME + "\n")
    (tmp_path / "public.md").write_text(SOMEONE_HOME + "\n")
    assert rules(tmp_path) == [("public.md", "home-path")]


def test_flags_sqlite_databases(tmp_path: Path) -> None:
    (tmp_path / "store.db").write_bytes(b"")
    (tmp_path / "cache.sqlite3").write_bytes(b"")
    (tmp_path / "blob.bin").write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    (tmp_path / "notes.txt").write_text("SQLite format 3 is the header\n")
    assert sorted(rules(tmp_path)) == [
        ("blob.bin", "sqlite-database"),
        ("cache.sqlite3", "sqlite-database"),
        ("store.db", "sqlite-database"),
    ]
