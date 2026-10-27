"""What happened to a session's pull requests after the agent was done with them.

Claude Code records a `pr-link` for every PR a session opens or pushes to, so the join
from session to PR is exact. For each PR retro asks `gh` for its reviews and checks and
turns two things into friction:

- a review that requested changes, or left a comment, from someone other than the PR author
- a check that finished as a failure

Calls go through the `gh` CLI so retro reuses whatever auth you already have. Responses
are cached on disk in the data directory, keyed by PR URL.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from retro.friction import Friction, FrictionKind
from retro.model import Session
from retro.redact import excerpt

Runner = Callable[[list[str]], str]
FIELDS = "number,author,state,mergedAt,reviews,statusCheckRollup"
_FAILED = {"FAILURE", "TIMED_OUT", "ERROR", "STARTUP_FAILURE"}


def gh(args: list[str]) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout


def fetch_pr(url: str, run: Runner = gh, cache_dir: Path | None = None) -> dict[str, Any]:
    cache = (
        cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json") if cache_dir else None
    )
    if cache and cache.exists():
        cached: dict[str, Any] = json.loads(cache.read_text())
        return cached
    data: dict[str, Any] = json.loads(run(["pr", "view", url, "--json", FIELDS]))
    # Only cache PRs that can no longer change.
    if cache and data.get("state") in ("MERGED", "CLOSED"):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
    return data


def pr_friction(session: Session, url: str, pr: dict[str, Any]) -> list[Friction]:
    number = pr.get("number", "?")
    author = (pr.get("author") or {}).get("login")
    anchor = len(session.events) - 1
    found: list[Friction] = []

    def emit(kind: FrictionKind, ref: str, when: object, evidence: str, detail: str) -> None:
        found.append(
            Friction(
                kind=kind,
                session_id=session.id,
                source=session.source,
                event_index=anchor,
                timestamp=_timestamp(when),
                project=session.project,
                skill=session.skill_at(anchor) if anchor >= 0 else None,
                evidence=excerpt(evidence),
                detail=detail,
                session_path=str(session.path),
                ref=ref,
            )
        )

    for i, review in enumerate(pr.get("reviews") or [], start=1):
        reviewer = (review.get("author") or {}).get("login")
        state, body = review.get("state"), (review.get("body") or "").strip()
        if reviewer == author:
            continue
        if state == "CHANGES_REQUESTED" or (state == "COMMENTED" and body):
            emit(
                FrictionKind.REVIEW_COMMENT,
                f"pr{number}.review{i}",
                review.get("submittedAt"),
                body or "(changes requested)",
                f"{url} {state.lower()} by {reviewer}",
            )

    for i, check in enumerate(pr.get("statusCheckRollup") or [], start=1):
        outcome = check.get("conclusion") or check.get("state")
        if outcome in _FAILED:
            name = check.get("name") or check.get("context") or "check"
            emit(
                FrictionKind.CHECK_FAILURE,
                f"pr{number}.check{i}",
                check.get("completedAt"),
                f"{name}: {outcome.lower()}",
                url,
            )
    return found


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
