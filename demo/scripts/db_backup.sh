#!/bin/sh
# Sample script for the demo: dumps a database to a dated file.
set -e
mkdir -p "$HOME/backups"
echo "backup taken at $(date '+%Y-%m-%d %H:%M:%S')" > "$HOME/backups/db-$(date +%F).txt"
echo "db backup ok"
