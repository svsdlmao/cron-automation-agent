#!/usr/bin/env bash
# cron-agent for Linux / WSL / macOS: ./start-agent.sh
# First run creates .venv and installs everything. Uses the real crontab when there is one,
# otherwise a file crontab plus the built-in daemon.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[setup] First run: creating .venv and installing cron-agent..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e ".[dev]"
fi

export CRON_AGENT_SCRIPT_DIRS="${CRON_AGENT_SCRIPT_DIRS:-$PWD/demo/scripts:$HOME/scripts:$HOME/bin:$HOME/.local/bin}"

if command -v crontab >/dev/null 2>&1; then
  if ! pgrep -x cron >/dev/null && ! pgrep -x crond >/dev/null; then
    echo "note: the cron daemon is not running, so jobs won't fire. Start it with: sudo service cron start"
  fi
  exec .venv/bin/python -m cron_agent "$@"
fi

echo "No crontab on this system: using a file crontab and the built-in cron-agent daemon."
export CRON_AGENT_BACKEND=file
.venv/bin/python -m cron_agent daemon > /dev/null 2>&1 &
DAEMON=$!
trap 'kill "$DAEMON" 2>/dev/null || true' EXIT
.venv/bin/python -m cron_agent "$@"
