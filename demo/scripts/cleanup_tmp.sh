#!/bin/sh
# Sample script for the demo: removes week-old files from /tmp.
find /tmp -maxdepth 1 -type f -mtime +7 -delete
echo "tmp cleanup ok"
