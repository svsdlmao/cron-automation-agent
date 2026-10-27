#!/usr/bin/env bash
# Runs inside the container: start the real cron daemon, run the suite against the
# real crontab binary, then prove a job installed by the agent is executed by crond.
set -euo pipefail

cron                                   # the daemon forks into the background
echo "== cron daemon: $(pgrep -x cron >/dev/null && echo running || echo NOT running)"

echo "== unit + MCP tests (file backend)"
su demo -c "cd /app && python3 -m pytest -q -p no:cacheprovider"

echo "== system-backend tests (real crontab binary)"
su demo -c "cd /app && CRON_AGENT_SYSTEM_TESTS=1 python3 -m pytest -q -p no:cacheprovider tests/test_system_backend.py"

echo "== live check: does crond really run a job the agent installed?"
su demo -c "cd /app && python3 docker/live_cron_check.py"
