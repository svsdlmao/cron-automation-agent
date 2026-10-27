# Cron & Automation Agent

Schedule jobs in plain English: the agent translates your request to cron, validates it, shows you
exactly what will change, and installs it into your own crontab only after you say yes.

Operating Systems project · Shauryaveer Singh Dabas (24BCE0416) · Guide: Prof. Naveenkumar

```
cron-agent> Run my database backup script every Friday at midnight.
Proposed new job e267
  + 0 0 * * 5 { /home/you/scripts/db_backup.sh; } >> /home/you/.cron-agent/logs/e267.log 2>&1 # cron-agent:id=e267
  meaning:   At 00:00, only on Friday
  next runs: Fri 23 Oct 2026 00:00 · Fri 30 Oct 2026 00:00 · Fri 06 Nov 2026 00:00
  note:      Using ~/scripts/db_backup.sh (matched “database backup”).
Add this job? [y/N] y
Installed job e267. Next run: Fri 23 Oct 2026 00:00. Backup 20261022-161005-936708 saved; say “undo” to revert.
```

## Quick start

**Windows:** double-click **`start-agent.bat`**. The first run creates `.venv` and installs everything.
Two windows open: the agent you type into, and the **cron-agent daemon**, a small cron that really
runs your jobs (Windows has no cron). Needs Python 3.10+ and Git for Windows, whose Git Bash runs the jobs.

**Linux / WSL / macOS:** `./start-agent.sh`. It uses the real `crontab` and cron daemon
(`sudo service cron start` if it isn't running).

A step-by-step presentation script is in **[docs/DEMO.md](docs/DEMO.md)**.

## How it works

A user-to-shell loop in three layers, with the OS work behind an MCP server:

| Layer | Module | Job |
|---|---|---|
| Interface | `repl.py` | `cron-agent>` prompt with history (prompt_toolkit); plain stdin when piped |
| Orchestration | `agent.py`, `planner.py`, `client.py` | Turns a request into **one** validated tool call, and asks for approval before any write. An MCP client over stdio |
| Tooling | `server.py` (MCP server `cron-tools`) | The only part that touches the OS: `crontab -l`, `crontab -`, the daemon's status, job logs |
| Scheduler | system cron, or `daemon.py` | Runs the jobs. `daemon.py` re-reads the crontab every minute and forks a shell for each due line, like crond |

Planners (`--provider`):

- `hybrid` (default): the offline rules first (instant, predictable); only requests the rules can't
  read go to an LLM: Claude if `ANTHROPIC_API_KEY` is set, otherwise a running Ollama. Without either it is
  the rules alone.
- `rules`: the offline translator (`translator.py`), no network at all.
- `ollama` / `anthropic` / `openai`: always ask that model.

LLM planners see the tools discovered from the MCP server at runtime, plus the current jobs and available
scripts. Their output is validated with pydantic, retried once with the exact error, and replaced by the
rules if still invalid.

### MCP tools

| Tool | Type | What it does |
|---|---|---|
| `list_jobs` | read | The agent's tagged jobs; other crontab lines are counted, never touched |
| `preview_schedule` | read | Validates an expression; English read-back and next run times |
| `cron_status` | read | Is the scheduler running; job, line and backup counts |
| `read_job_log` | read | Last lines of a job's output log |
| `find_script` | read | Maps “database backup” to `~/scripts/db_backup.sh` |
| `add_job` | write | Snapshot, merge, tag, escape `%`, pipe into `crontab -`, verify |
| `remove_job` | write | Removes one tagged job |
| `rollback` | write | Restores a backup (and takes one first, so it can be undone too) |

Every write is two-phase: a `dry_run` returns the exact diff and a hash of the crontab it was computed
from; the real call must present that hash, so a crontab edited in between is never overwritten.

## Safety model

- **Human in the loop**: write tools always preview first and need `y`; the gate is in the client.
- **The model never writes shell**: it fills `schedule` and `command`; deterministic code builds the line.
- **Blocklist** (`safety.py`): `rm -rf /`, `mkfs`, `dd of=/dev/…`, `curl … | sh`, fork bombs, `sudo`/`su`,
  `/etc/crontab`, `crontab -r`, shutdown/reboot, recursive chmod/chown on `/`.
- **Sanitization**: newlines and control characters rejected (no hidden second crontab line), every `%`
  escaped to `\%`, the agent's own tag can't be spoofed, every cron field checked by a strict parser.
- **Never overwrites**: only lines tagged `# cron-agent:id=…` change; a backup before every write;
  read-back verification with automatic restore; a file lock (`flock`) serialises writers.
- **Least privilege**: runs as you; never sudo, never another user's crontab.

Cron's own traps are handled: “first Monday of the month” (cron ORs the day fields, so a date check is
added and the preview shows only real run days), “last day of the month”, “last Friday”, “every 90
minutes” (two lines), sub-minute and fortnightly schedules (it asks), one-off jobs (points you to `at`).

## Commands

```bash
cron-agent                         # the agent (hybrid planner; real crontab on Linux)
cron-agent --backend file daemon   # the built-in scheduler for the file crontab (Windows, demos)
cron-agent --provider rules        # offline rules only
cron-agent eval                    # accuracy + LLM error recovery on eval/prompts.jsonl
cron-agent bench --reps 10         # time-to-execution: request -> installed and verified
cron-agent server                  # just the MCP server, e.g. for another MCP client
```

Things to say: `what's scheduled?`, `is cron running?`, `did the backup run?`,
`explain */15 9-16 * * 1-5`, `remove job e267`, `undo`, `help`.

Environment variables: `CRON_AGENT_HOME` (default `~/.cron-agent`), `CRON_AGENT_BACKEND`
(`auto|system|file`), `CRON_AGENT_PROVIDER`, `CRON_AGENT_MODEL`, `CRON_AGENT_SCRIPT_DIRS`
(default `~/scripts:~/bin:~/.local/bin`), `OLLAMA_HOST`, `ANTHROPIC_API_KEY`.

## Tests, CI and evaluation

```bash
pytest -q                                                       # ~160 tests: unit, MCP stdio, agent, daemon
CRON_AGENT_SYSTEM_TESTS=1 pytest tests/test_system_backend.py   # real crontab (rewrites yours, then restores)
python docker/live_cron_check.py                                # real crond runs an agent-installed job
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every push: on **Ubuntu with the real cron daemon**
(the suite, the real-crontab tests, and the live check) and on **Windows** with the built-in daemon.
`docker/Dockerfile` does the same Linux check locally.

Measured on a Windows 11 laptop: the rules planner scores 59/59 on the labelled set and 18/20 on
held-out paraphrases, and a request goes from typed to installed in a median of about 20 ms. Llama 3.2 3B
via Ollama scores 46% / 35% on its own; 16 of its 20 invalid answers still ended correctly through
retry-with-the-error and rule fallback, and no invalid line ever reached the crontab. Full numbers:
`eval/results/`.

## Project layout

```
cron_agent/   agent.py client.py config.py cronexpr.py crontab.py daemon.py evaluate.py jobs.py
              planner.py providers.py repl.py safety.py scripts.py server.py translator.py benchmark.py
tests/        pytest suite (file backend by default)
eval/         prompts.jsonl (59 labelled), heldout.jsonl (20 held-out), results/
docker/       Dockerfile and the real-cron live check
demo/         sample scripts and scripted sessions (session, chained, malicious -> *_transcript.txt)
docs/         DEMO.md, screenshots, and the Review 1–3 slides as one PDF
```

## Limitations

- The rules cover common English phrasings; unusual ones get a question, the LLM (hybrid mode), or a
  literal cron expression.
- Date-check guards use GNU `date -d` (Linux, and Git Bash on Windows).
- Per-user crontab only: no `/etc/crontab`, systemd timers or launchd.
