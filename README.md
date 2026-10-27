# retro

retro reads the session logs your coding agents leave on disk and finds the places where they got it wrong. It reads Claude Code, Codex, Cursor and OpenCode. The agent CLIs you already have installed then label those mistakes, group them into recurring patterns and propose edits to the skills and CLAUDE.md/AGENTS.md files that should have prevented them.

Then retro checks whether an edit actually helps. Each mistake can become a regression case that fails on the session where it happened. A proposed edit counts as a fix only if replaying the cases with it turns failing cases into passing ones, including cases it wasn't tuned on.

It all runs on your machine. retro has no API keys and no server. Its AI calls go through the headless modes of `claude`, `codex`, `cursor-agent` and `opencode`, using whatever login you already have.

<p align="center">
  <img src="docs/img/friction-flow.svg" alt="Sankey chart from mistake category to recurring pattern to the instruction file that should change" width="100%">
</p>

Every chart in this README comes from `retro demo`, which generates synthetic sessions. None of them show anyone's real data. To see the full report:

```sh
git clone https://github.com/PDiTO/retro && cd retro
uv sync
uv run retro demo --open
```

## Using it on your own sessions

```sh
uv run retro agents --probe      # which agent CLIs you have, and whether they answer
uv run retro scan --days 30      # read sessions and find friction; no AI calls
uv run retro analyze --panel     # agents label, cluster and propose; asks before sending anything
uv run retro report --html --open
```

```
$ retro agents --probe
agent                version                sessions  backend  probe
claude Claude Code   2.1.310 (Claude Code)  read      tested   ok 5s
codex Codex          codex-cli 0.164.0      read      tested   ok 16s
cursor Cursor Agent  2026.10.01-e373342     read      tested   ok 30s
opencode OpenCode    1.19.4                 read      tested   ok 5s
```

`scan` reads the logs and detects friction without calling any model. `analyze` is the first step that does, and it tells you which agents it will use and how many calls it expects before it starts. Everything it produces stays in `~/.local/share/retro`.

## Why I built this

I run a lot of agent sessions, across several agents, and the same mistakes keep coming back. The agent commits before running the tests, tries a force push, or ignores a style rule that a skill spells out.

When I noticed one, I'd add a line to a skill or to CLAUDE.md, from memory, and move on. I never found out whether the line helped, or whether it made something else worse, because nothing checked. An edit to a prompt is a code change with no test, and I'd never accept that for code.

So I wanted the loop I'd use for a bug. Find it in the logs, write a test that reproduces it and watch the test fail. Then change the instructions and watch the test pass, along with everything else.

## What the report shows

**When friction happens.** A contributions-style calendar, with sessions per week above it so a quiet day with no sessions isn't mistaken for a good day.

<img src="docs/img/friction-calendar.svg" alt="Calendar heatmap of friction events per day" width="100%">

**What kind, and whether it's getting better.** Weekly counts by detector, plus the rate per 100 sessions underneath, so a busy week doesn't look like a bad one.

<img src="docs/img/friction-by-kind.svg" alt="Stacked weekly friction by kind with a rate line" width="100%">

**Which skills go wrong.** Ranking skills by event count mostly ranks them by how often they run. The scorecard divides by agent turns spent inside each skill and draws a 95% Wilson interval. A rarely used skill with two bad sessions doesn't get called the worst on thin evidence, and the busiest skill isn't automatically the culprit.

<img src="docs/img/skill-scorecard.svg" alt="Friction rate per skill with confidence intervals" width="100%">

**The worst sessions.** Each row is one session, with a tick where each friction event happened along it.

<img src="docs/img/session-barcode.svg" alt="Barcode chart of friction events along the worst sessions" width="100%">

**How much the agents agree.** With `--panel`, every agent you have labels every event on its own. Pairwise agreement and Cohen's κ show how far one model's opinion can be trusted, and the majority vote is what the clustering uses.

<img src="docs/img/agent-agreement.svg" alt="Agreement heatmap between labelling agents and their category mix" width="100%">

The HTML report puts these together with the pattern table and each proposed change as a coloured diff with its draft regression check. It's one offline file with no scripts and no network requests: [docs/img/report.html](docs/img/report.html).

## How the AI part works

retro doesn't call a model API. For each detected agent it shells out to the agent's own headless mode, with one of two kinds of call:

| | `ask`: one read-only question, JSON answer | `run_task`: a full agent run |
| :--- | :--- | :--- |
| Used for | labelling, clustering, proposals, `judge` checks | replaying a case, writing a candidate edit |
| Claude Code | `claude -p --output-format json --json-schema … --tools ""` | `claude -p … --permission-mode bypassPermissions` |
| Codex | `codex exec -s read-only --output-schema … -o last.txt` | `codex exec --json -s workspace-write -C <worktree>` |
| Cursor | `cursor-agent -p --output-format json --mode ask` | `cursor-agent -p --force --workspace <worktree>` |
| OpenCode | `opencode run --format json --agent plan` | `opencode run --format json --dir <worktree>` |

`ask` calls run with tools off, or in a read-only mode, so a prompt built from transcript excerpts can't make an agent act on your machine. `run_task` only ever runs inside a throwaway git worktree. Claude and Codex enforce the JSON Schema natively. The others get the schema in the prompt, and retro pulls the first JSON object out of the reply and checks its required keys. Gemini CLI and Grok CLI backends exist too, but they're marked untested because I haven't run them.

`retro analyze` makes three passes:

1. **Label.** Each friction event gets a category (ignored an instruction, skipped verification, wrong approach, scope creep, risky action, tool misuse, style, or not a mistake), a confidence and a one-line summary.
2. **Cluster.** The real mistakes are grouped into recurring patterns, and each pattern is pinned on one skill or instruction file. `retro skills` lists the candidates, found in each agent's skill directories and de-duplicated across symlinks.
3. **Propose.** For each pattern, the agent reads the target file and writes the smallest diff that would prevent it, plus a draft regression check.

## Finding friction

Detection uses no model. It reads the transcripts and flags:

| Kind | Signal |
| :--- | :--- |
| `correction` | a later prompt that opens with pushback ("no,", "you didn't", "why did you", "I said"), unless it answers a question the agent just asked |
| `interrupt` | the human stopped the agent mid-turn |
| `denial` | a refused tool call, by the human or a permission classifier |
| `error_streak` | three or more failed tool calls in a row |
| `review_comment` | with `--github`, a review on the session's PR from someone other than the author |
| `check_failure` | with `--github`, a failed CI check on the session's PR |

These are cheap signals, and some will be wrong. That's why the labelling pass has a `not_a_mistake` category and why the panel exists. How much of each signal survives depends on the agent's log format:

| | Claude Code | Codex | Cursor | OpenCode |
| :--- | :---: | :---: | :---: | :---: |
| prompts, tool calls, errors | yes | yes | yes | yes |
| interrupts | yes | yes | inferred | yes |
| denials | yes | no | yes | yes |
| skill in use | yes | no | no | no |
| linked PRs | yes | no | no | no |

Cursor and OpenCode keep sessions in SQLite. retro opens those databases read-only.

## Regression cases

A case records one mistake as checks:

```toml
id = "tests-before-commit"
title = "Committed without running the test suite first"
friction = "5e55i0n0:7"
prompt = "Add a --verbose flag to the widget CLI and commit it"

[[checks]]
type = "require_tool"
tool = "Bash"
pattern = "pytest"
before = "git commit"
```

| Check | Passes when |
| :--- | :--- |
| `forbid_tool` | no tool call matches the tool and pattern |
| `require_tool` | a matching call happens, and happens before any call matching `before` if that's set |
| `forbid_text` | the pattern appears in neither the agent's messages nor the files it wrote, depending on `where` |
| `max_errors` | at most `limit` tool calls failed |
| `command` | a shell command exits 0 in the replay workspace |
| `judge` | an agent, asked headless, says the rubric is met |

**A case has to fail on the session it came from before it counts.** `retro case check --admit` enforces this. A check that passes on the transcript where the mistake happened doesn't capture the mistake, for the same reason a regression test has to fail before the fix. Without that rule it's easy to write a check that looks right and catches nothing.

```
$ retro case check examples/cases/tests-before-commit.toml mistake.jsonl --admit
fail runs pytest before git commit  no matching call before event 4
admitted: the case catches the original mistake

$ retro case check examples/cases/tests-before-commit.toml fixed.jsonl
pass runs pytest before git commit  event 5: [Bash] uv run pytest
```

`retro case new <friction-id>` drafts a case from a friction event. Each proposal from `analyze` also includes a draft check in this format.

<img src="docs/img/case-grid.svg" alt="Grid of regression cases with pass rates before and after an edit" width="100%">

## Hill climbing

`retro replay <case>` re-runs one case. It checks out a git worktree at the case's commit, lays a candidate instructions directory over it, runs the agent headless on the original prompt, finds the transcript the agent just wrote, and grades it with the same checks.

`retro climb <cases-dir>` runs the whole loop:

1. Split the admitted cases into train, val and test by a hash of each id, so adding cases never moves an existing one.
2. Replay the train cases and give the failures to an agent, which edits a copy of the instructions.
3. Replay the val cases with that candidate. Keep it only if the mean pass rate rises and no val case gets worse.
4. Repeat from whatever was kept.
5. Score the test cases once, at the end, with the starting and the final instructions.

Nothing in the loop sees the test split, so that last number is the honest answer to whether the edits generalise or just fit the cases they were written from. Replays are noisy, so every case runs several seeds and scores are pass rates. `climb` prints how many headless runs it will make before it starts.

<img src="docs/img/climb.svg" alt="Hill-climbing steps with val and train pass rates, and held-out test cases before and after" width="100%">

## Privacy

Agent transcripts hold API keys, client code and whatever else passed through a terminal, so retro treats them as private:

- **Storage.** Everything derived from your sessions goes in `$RETRO_HOME`, which defaults to `~/.local/share/retro`. retro refuses to use a data directory inside a git work tree.
- **Redaction.** Every excerpt goes through `retro.redact` before it's printed, saved or sent to an agent. That strips API keys, tokens, private keys, JWTs, 32-byte hex secrets, `KEY=value` secrets and email addresses, and rewrites home directories to `~`. It's a pattern list, so it can miss things.
- **What leaves the machine.** `scan` sends nothing anywhere. `analyze` sends redacted excerpts to the agents you pick, and for proposals it also sends the target instruction file, which that agent already reads every session. It asks before starting unless you pass `--yes`.
- **`retro audit`.** It flags secrets, real-looking home paths, SQLite files and anything shaped like a transcript in a directory. This repo's CI runs it on every push.

## Commands

| Command | |
| :--- | :--- |
| `retro agents [--probe]` | installed agent CLIs, and whether each one answers a test prompt |
| `retro skills` | skills and instruction files retro can propose edits to |
| `retro scan [--days N] [--source NAME] [--github] [--exclude REGEX]` | read sessions and find friction |
| `retro analyze [--agent NAME] [--panel] [--limit N]` | label, cluster and propose |
| `retro report [--html] [--open]` | terminal summary, or the HTML report |
| `retro demo [--open]` | the HTML report on synthetic data |
| `retro case new <id>` / `retro case check <case> <session> [--admit] [--judge NAME]` | write and grade regression cases |
| `retro replay <case> --instructions DIR` | replay one case with a headless agent |
| `retro climb <cases-dir> --instructions DIR` | hill-climb an instructions directory |
| `retro audit [path]` | check a directory before publishing it |

## Limits

- The friction detectors are pattern rules. The labelling pass catches false positives, but anything the rules miss never reaches it.
- Skill attribution and PR links only exist in Claude Code logs. For other agents, patterns get pinned on instruction files from what the sessions say.
- Replays lay candidate instructions over the project root, so they test project-level CLAUDE.md, AGENTS.md and `.claude/skills/`. Global skills under your home directory are still loaded alongside them.
- Headless runs cost money or subscription quota. `analyze --limit` caps the events sent, and `climb` asks before it starts.

## Layout

```
src/retro/
  model.py                Session and Event, the source-neutral view of a session
  sources/                one adapter per agent's log format, plus GitHub PR data via gh
  friction.py             the friction detectors
  agents.py               detection and headless backends for each agent CLI
  skills.py               the inventory of skills and instruction files
  analyze.py              label, cluster and propose prompts, and the judge
  insights.py             what the report renders, saved as JSON
  cases.py                case TOML, checks, grading, admission
  replay.py               worktree replays and the agent proposer
  climb.py                train/val/test split, accept/reject, the loop
  viz/                    SVG charts and the HTML report, stdlib only
  demo.py                 the synthetic data behind `retro demo`
  redact.py, audit.py     scrubbing, and the pre-publish check
  store.py, report.py, cli.py
examples/cases/           three cases graded against the test fixtures
scripts/                  regenerate the test fixtures and the README images
```

## Tests

```sh
uv run retro audit .
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

The tests never call a real agent. The backends are tested against recorded CLI output shapes, and analysis, replay and climbing against scripted fake agents. The fixtures are synthetic sessions written by `scripts/make_fixtures.py`, plus SQLite stores built at test time.
