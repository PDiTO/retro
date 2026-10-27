"""`retro` command line.

retro agents [--probe]          which agent CLIs are installed, and whether they answer
retro skills                    the skills and instruction files retro can propose edits to
retro scan                      read sessions from every detected agent, find friction
retro analyze                   have agents label friction, find patterns, propose changes
retro report [--html]           terminal summary, or the HTML report with charts
retro demo                      the HTML report on synthetic data, no setup needed
retro case new <friction-id>    draft a regression case from one friction event
retro case check <case> <session> [--admit]
retro replay <case>             re-run a case with a headless agent and grade it
retro climb <cases-dir>         hill-climb an instructions directory against cases
retro audit [path]              check a directory for secrets and transcripts
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import webbrowser
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from retro import agents, cases, report, skills, store, viz
from retro.analyze import analyze, judge_with
from retro.audit import audit
from retro.climb import ClimbResult, climb, split
from retro.demo import build_demo
from retro.friction import Friction, FrictionKind, detect
from retro.insights import ClimbRun, ClimbStep, Insights, session_stats
from retro.model import Session
from retro.redact import excerpt, redact
from retro.replay import AgentProposer, WorktreeRunner
from retro.sources import SOURCES, github, load_session

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    console = Console()
    try:
        return int(args.func(args, console))
    except store.UnsafeDataDir as e:
        console.print(f"[red]{escape(str(e))}[/]")
        return 2
    except agents.AgentError as e:
        console.print(f"[red]agent error:[/] {escape(str(e))}")
        return 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retro", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(required=True, metavar="command")
    source_names = [s.name for s in SOURCES]
    agent_names = list(agents.BACKENDS)

    ag = sub.add_parser("agents", help="list installed agent CLIs")
    ag.add_argument("--probe", action="store_true", help="send each one a tiny prompt")
    ag.set_defaults(func=_agents)

    sk = sub.add_parser("skills", help="list skills and instruction files")
    sk.add_argument("--project", type=Path, action="append", default=[])
    sk.set_defaults(func=_skills)

    scan = sub.add_parser("scan", help="read sessions, detect friction, print a report")
    scan.add_argument(
        "--days", type=int, default=30, help="only sessions touched in the last N days (default 30)"
    )
    scan.add_argument(
        "--source",
        action="append",
        choices=source_names,
        metavar="NAME",
        help=f"only these sources ({', '.join(source_names)}); default: all found",
    )
    scan.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="read a source from somewhere other than its default location",
    )
    scan.add_argument(
        "--github", action="store_true", help="also fetch reviews and checks for linked PRs via gh"
    )
    scan.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="REGEX",
        help="skip sessions whose working directory matches, e.g. automated runs",
    )
    scan.add_argument("--examples", type=int, default=3)
    scan.set_defaults(func=_scan)

    an = sub.add_parser("analyze", help="label friction, find patterns, propose changes")
    an.add_argument("--agent", choices=agent_names, help="lead agent (default: first found)")
    an.add_argument("--panel", action="store_true", help="every detected agent labels every event")
    an.add_argument("--model", help="model for the lead agent")
    an.add_argument("--limit", type=int, default=150, help="most recent N events (default 150)")
    an.add_argument("--no-propose", action="store_true")
    an.add_argument("--project", type=Path, action="append", default=[])
    an.add_argument("--yes", "-y", action="store_true", help="don't ask before sending excerpts")
    an.set_defaults(func=_analyze)

    rep = sub.add_parser("report", help="print the last scan, or write the HTML report")
    rep.add_argument("--kind", choices=[k.value for k in FrictionKind])
    rep.add_argument("--skill")
    rep.add_argument("--examples", type=int, default=3)
    rep.add_argument("--html", action="store_true", help="write report.html and chart SVGs")
    rep.add_argument("--out", type=Path, help="directory for --html (default: <data dir>/report)")
    rep.add_argument("--open", action="store_true", help="open the HTML report in a browser")
    rep.set_defaults(func=_report)

    demo = sub.add_parser("demo", help="write the HTML report for synthetic demo data")
    demo.add_argument("--out", type=Path, default=Path("retro-demo"))
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--open", action="store_true")
    demo.set_defaults(func=_demo)

    case = sub.add_parser("case", help="regression cases").add_subparsers(
        required=True, metavar="action"
    )
    new = case.add_parser("new", help="draft a case from a friction event")
    new.add_argument("friction_id")
    new.add_argument("--out", type=Path, help="directory to write to (default: <data dir>/cases)")
    new.set_defaults(func=_case_new)
    check = case.add_parser("check", help="grade a session against a case")
    check.add_argument("case", type=Path)
    check.add_argument("session", type=Path)
    check.add_argument(
        "--admit",
        action="store_true",
        help="succeed only if the case catches a mistake in this session",
    )
    check.add_argument("--workdir", type=Path, help="run command checks here")
    check.add_argument("--judge", choices=agent_names, help="grade judge checks with this agent")
    check.set_defaults(func=_case_check)

    rp = sub.add_parser("replay", help="re-run a case with a headless agent and grade it")
    rp.add_argument("case", type=Path)
    _replay_args(rp, agent_names)
    rp.set_defaults(func=_replay)

    cl = sub.add_parser("climb", help="hill-climb an instructions directory against cases")
    cl.add_argument("cases", type=Path, help="directory of admitted case files")
    _replay_args(cl, agent_names)
    cl.add_argument("--steps", type=int, default=4)
    cl.add_argument("--max-drop", type=float, default=0.0)
    cl.add_argument("--yes", "-y", action="store_true", help="don't ask before starting")
    cl.set_defaults(func=_climb)

    aud = sub.add_parser("audit", help="check a directory for secrets, home paths and transcripts")
    aud.add_argument("path", type=Path, nargs="?", default=Path())
    aud.set_defaults(func=_audit)
    return parser


def _replay_args(p: argparse.ArgumentParser, agent_names: list[str]) -> None:
    p.add_argument("--agent", choices=agent_names, default="claude")
    p.add_argument("--model")
    p.add_argument(
        "--instructions",
        type=Path,
        required=True,
        help="directory laid out like a project root: CLAUDE.md, AGENTS.md, .claude/skills/...",
    )
    p.add_argument("--repo", type=Path, help="repo to replay in, if the case doesn't name one")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--keep", action="store_true", help="keep the worktrees afterwards")


def _agents(args: argparse.Namespace, console: Console) -> int:
    found = agents.detect()
    if not found:
        console.print(f"no agent CLIs found on PATH; retro knows {', '.join(agents.BACKENDS)}")
        return 1
    readable = {s.name for s in SOURCES}
    table = Table(box=None, pad_edge=False)
    for col in ("agent", "version", "sessions", "backend", *(["probe"] if args.probe else [])):
        table.add_column(col)
    for d in found:
        source = {"claude": "claude-code"}.get(d.name, d.name)
        row = [
            f"[bold]{d.name}[/] [dim]{escape(d.label)}[/]",
            escape(d.version),
            "[green]read[/]" if source in readable else "[dim]not yet[/]",
            "[green]tested[/]" if d.tested else "[yellow]untested[/]",
        ]
        if args.probe:
            with console.status(f"asking {d.name}…"):
                try:
                    reply = agents.get(d.name).ask("What is 6*7?", PROBE_SCHEMA, timeout=240)
                    ok = (reply.data or {}).get("answer") == 42
                    row.append(
                        f"[green]ok[/] {reply.seconds:.0f}s" if ok else "[red]wrong answer[/]"
                    )
                except agents.AgentError as e:
                    row.append(f"[red]{escape(str(e))[:60]}[/]")
        table.add_row(*row)
    console.print(table)
    return 0


def _skills(args: argparse.Namespace, console: Console) -> int:
    items = skills.inventory(args.project)
    table = Table(box=None, pad_edge=False)
    for col in ("kind", "name", "loaded by", "path"):
        table.add_column(col)
    for i in items:
        table.add_row(
            i.kind, escape(i.name), ", ".join(i.agents), f"[dim]{escape(i.display_path)}[/]"
        )
    console.print(table)
    console.print(f"[dim]{len(items)} instruction sources[/]")
    return 0


def _scan(args: argparse.Namespace, console: Console) -> int:
    root = store.data_dir()
    since = datetime.now(UTC) - timedelta(days=args.days)
    exclude = [re.compile(p) for p in args.exclude]
    found: list[Friction] = []
    stats = []
    with console.status("reading sessions…"):
        for session in _sessions(args, since):
            if session.cwd and any(p.search(session.cwd) for p in exclude):
                continue
            if not session.prompts:
                continue
            stats.append(session_stats(session))
            found.extend(detect(session))
            if args.github:
                found.extend(_github_friction(session, root, console))
    store.save_friction(root, found)
    insights = Insights(
        generated_at=datetime.now(UTC),
        window_days=args.days,
        agents_detected=[d.name for d in agents.detect()],
        sessions=stats,
        friction=found,
    )
    path = insights.save(store.insights_path(root))
    report.render(found, len(stats), console, examples=args.examples)
    console.print(f"[dim]saved to {escape(redact(str(path)))}[/]")
    return 0


def _sessions(args: argparse.Namespace, since: datetime) -> Iterator[Session]:
    roots = dict(r.split("=", 1) for r in args.root)
    for source in SOURCES:
        if args.source and source.name not in args.source:
            continue
        root = (
            Path(roots[source.name]).expanduser() if source.name in roots else source.default_root
        )
        if root.exists():
            yield from source.iter_sessions(root, since)


def _github_friction(session: Session, root: Path, console: Console) -> list[Friction]:
    found: list[Friction] = []
    for url in session.pull_requests:
        try:
            pr = github.fetch_pr(url, cache_dir=root / "github")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
            console.print(f"[yellow]skipped {escape(url)}: {escape(type(e).__name__)}[/]")
            continue
        found.extend(github.pr_friction(session, url, pr))
    return found


def _report(args: argparse.Namespace, console: Console) -> int:
    root = store.data_dir()
    path = store.insights_path(root)
    if not path.exists():
        console.print("nothing saved yet; run [bold]retro scan[/] first")
        return 1
    insights = Insights.load(path)
    if args.html:
        return _write_html(insights, args.out or root / "report", args.open, console)
    found = insights.friction
    if args.kind:
        found = [f for f in found if f.kind.value == args.kind]
    if args.skill:
        found = [f for f in found if f.skill == args.skill]
    report.render(found, len(insights.sessions), console, examples=args.examples)
    if insights.patterns:
        report.render_patterns(insights, console)
    return 0


def _write_html(insights: Insights, out: Path, open_it: bool, console: Console) -> int:
    files = viz.export(insights, out)
    html = next(f for f in files if f.suffix == ".html")
    console.print(f"wrote {escape(str(html))} and {len(files) - 1} charts")
    if open_it:
        webbrowser.open(html.resolve().as_uri())
    return 0


def _demo(args: argparse.Namespace, console: Console) -> int:
    return _write_html(build_demo(args.seed), args.out, args.open, console)


def _analyze(args: argparse.Namespace, console: Console) -> int:
    root = store.data_dir()
    path = store.insights_path(root)
    if not path.exists():
        console.print("nothing to analyze; run [bold]retro scan[/] first")
        return 1
    insights = Insights.load(path)
    found = [d for d in agents.detect() if d.tested]
    if not found:
        console.print("no supported agent CLI found; see [bold]retro agents[/]")
        return 1
    lead = args.agent or found[0].name
    names = [lead, *(d.name for d in found if d.name != lead)] if args.panel else [lead]
    panel = [agents.get(n, model=args.model if n == lead else None) for n in names]
    inventory = skills.inventory(args.project)
    n = min(len(insights.friction), args.limit)
    batches = -(-n // 25)
    calls = batches * len(panel) + 1 + (0 if args.no_propose else 10)
    console.print(
        f"analyzing [bold]{n}[/] friction events with [bold]{', '.join(names)}[/] "
        f"(about {calls} headless calls). Redacted excerpts, and for proposals the target "
        "instruction file, go to these agents' providers."
    )
    if not args.yes and not _confirm(console):
        return 1
    with console.status("analyzing…") as status:
        result = analyze(
            insights,
            panel,
            inventory,
            limit=args.limit,
            propose_changes=not args.no_propose,
            progress=lambda msg: status.update(escape(msg)),
        )
    result.save(path)
    report.render_patterns(result, console)
    console.print("[dim]see the charts with[/] retro report --html --open")
    return 0


def _confirm(console: Console) -> bool:
    if not sys.stdin.isatty():
        console.print("not a terminal; pass --yes to go ahead")
        return False
    return console.input("go ahead? [y/N] ").strip().lower() in ("y", "yes")


def _case_new(args: argparse.Namespace, console: Console) -> int:
    root = store.data_dir()
    match = next((f for f in store.load_friction(root) if f.id == args.friction_id), None)
    if match is None:
        console.print(f"no friction with id {escape(args.friction_id)}; see [bold]retro report[/]")
        return 1
    prompt = ""
    if match.session_path and Path(match.session_path).exists():
        session = load_session(Path(match.session_path))
        prompt = redact(session.prompts[0].text) if session.prompts else ""
    out_dir = args.out or root / "cases"
    if args.out is None:
        store.ensure_private(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{match.id.replace(':', '-')}.toml"
    if path.exists():
        console.print(f"{escape(str(path))} already exists")
        return 1
    path.write_text(draft_case(match, prompt), encoding="utf-8")
    console.print(f"wrote {escape(str(path))}")
    console.print("edit the checks, then confirm it catches the original mistake with:")
    session_path = match.session_path or "<session.jsonl>"
    console.print(f"  retro case check {escape(str(path))} {escape(session_path)} --admit")
    return 0


def draft_case(friction: Friction, prompt: str) -> str:
    check = _CHECK_TEMPLATES[friction.kind]
    return _CASE_TEMPLATE.format(
        id=friction.id.replace(":", "-"),
        kind=friction.kind.value,
        friction=friction.id,
        prompt=_toml_string(prompt),
        evidence=excerpt(friction.evidence, 200),
        context=excerpt(friction.context, 200),
        check=check,
    )


_CASE_TEMPLATE = """\
id = "{id}"
title = "TODO: one line on what went wrong"
friction = "{friction}"
prompt = {prompt}
# repo = "~/path/to/repo"   # needed for replay
# base = "<commit>"          # commit the session started from

# What happened ({kind}):
#   agent: {context}
#   human: {evidence}
#
# Write checks that FAIL on the original session and would PASS if the mistake didn't
# happen. Check types: forbid_tool, require_tool, forbid_text, max_errors, command, judge.

[[checks]]
{check}
"""

# A starting check per friction kind. The TODO placeholders won't catch anything, so the
# draft won't pass `case check --admit` until someone writes a real check.
_CHECK_TEMPLATES = {
    FrictionKind.CORRECTION: 'type = "forbid_text"\nwhere = "writes"\npattern = "TODO"',
    FrictionKind.DENIAL: 'type = "forbid_tool"\npattern = "TODO"',
    FrictionKind.INTERRUPT: 'type = "forbid_tool"\npattern = "TODO"',
    FrictionKind.ERROR_STREAK: 'type = "max_errors"\nlimit = 2',
    FrictionKind.REVIEW_COMMENT: 'type = "judge"\nrubric = "TODO: what the reviewer flagged"',
    FrictionKind.CHECK_FAILURE: 'type = "command"\nrun = "TODO: the failing check, run locally"',
}


def _toml_string(text: str) -> str:
    if "'''" not in text and "\r" not in text:
        return "'''\n" + text + "'''"
    return (
        '"'
        + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        + '"'
    )


def _case_check(args: argparse.Namespace, console: Console) -> int:
    case = cases.Case.load(args.case)
    session = load_session(args.session)
    judge = None
    if args.judge:
        judge = judge_with(agents.get(args.judge))
    if args.admit:
        results = cases.grade(case, session, judge=judge)
        admitted = any(r.verdict is cases.Verdict.FAIL for r in results)
    else:
        results = cases.grade(case, session, workdir=args.workdir, judge=judge)
        admitted = cases.passed(results)
    _print_results(results, console)
    if args.admit:
        console.print(
            "[green]admitted[/]: the case catches the original mistake"
            if admitted
            else "[red]not admitted[/]: no check fails on the original session"
        )
    return 0 if admitted else 1


def _print_results(results: list[cases.CheckResult], console: Console) -> None:
    colours = {cases.Verdict.PASS: "green", cases.Verdict.FAIL: "red", cases.Verdict.SKIP: "yellow"}
    for r in results:
        colour = colours[r.verdict]
        console.print(
            f"[{colour}]{r.verdict.value:>4}[/] {escape(r.check.label)}  [dim]{escape(r.reason)}[/]"
        )


def _replay(args: argparse.Namespace, console: Console) -> int:
    case = cases.Case.load(args.case)
    runner = WorktreeRunner(agents.get(args.agent, model=args.model), args.repo, keep=args.keep)
    wins = 0
    try:
        for seed in range(args.seeds):
            with console.status(f"replaying {escape(case.id)} with {args.agent}, run {seed + 1}"):
                replay = runner.run(case, args.instructions, seed)
            results = cases.grade(case, replay.session, workdir=replay.workdir)
            ok = cases.passed(results)
            wins += ok
            console.print(f"run {seed + 1}: {'[green]pass[/]' if ok else '[red]fail[/]'}")
            _print_results(results, console)
    finally:
        runner.cleanup()
    console.print(f"[bold]{wins}/{args.seeds}[/] replays passed")
    return 0 if wins == args.seeds else 1


def _climb(args: argparse.Namespace, console: Console) -> int:
    loaded = [cases.Case.load(p) for p in sorted(args.cases.glob("*.toml"))]
    if len(loaded) < 4:
        console.print("climbing needs at least 4 admitted cases (train, val and test splits)")
        return 1
    parts = split(loaded)
    runs = args.seeds * (
        len(parts.val) + args.steps * (len(parts.train) + len(parts.val)) + 2 * len(parts.test)
    )
    console.print(
        f"{len(loaded)} cases: {len(parts.train)} train, {len(parts.val)} val, "
        f"{len(parts.test)} test. Up to [bold]{runs}[/] headless {args.agent} runs "
        f"plus {args.steps} proposals."
    )
    if not args.yes and not _confirm(console):
        return 1
    agent = agents.get(args.agent, model=args.model)
    runner = WorktreeRunner(agent, args.repo, keep=args.keep)
    proposer = AgentProposer(agent)
    try:
        with console.status("climbing…"):
            result = climb(
                runner,
                proposer,
                loaded,
                args.instructions,
                steps=args.steps,
                seeds=args.seeds,
                max_drop=args.max_drop,
            )
    finally:
        runner.cleanup()
    for i, d in enumerate(result.steps, start=1):
        mark = "[green]accepted[/]" if d.accept else "[dim]rejected[/]"
        console.print(f"step {i}: {mark} {escape(d.reason)}")
    before = sum(result.test_before.values()) / max(1, len(result.test_before))
    after = sum(result.test_after.values()) / max(1, len(result.test_after))
    console.print(f"held-out test pass rate: [bold]{before:.2f} -> {after:.2f}[/]")
    console.print(f"final instructions: {escape(str(result.instructions))}")
    _save_climb(result, args.instructions, console)
    return 0


def _save_climb(result: ClimbResult, target: Path, console: Console) -> None:
    root = store.data_dir()
    path = store.insights_path(root)
    if not path.exists():
        return
    insights = Insights.load(path)
    steps = [
        ClimbStep(i, d.accept, train, d.candidate, d.reason)
        for i, (d, train) in enumerate(zip(result.steps, result.train, strict=True), start=1)
    ]
    run = ClimbRun(
        str(target),
        steps,
        result.test_before,
        result.test_after,
        val_before=result.val_before,
        splits=result.splits,
    )
    insights.climbs.append(run)
    insights.save(path)


def _audit(args: argparse.Namespace, console: Console) -> int:
    findings = audit(args.path)
    for f in findings:
        console.print(f"[red]{f.rule}[/] {escape(str(f.path))}:{f.line}")
    if findings:
        console.print(f"[red]{len(findings)} finding(s)[/]")
        return 1
    console.print("[green]clean[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
