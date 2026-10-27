#!/usr/bin/env python3
"""Sample script for the demo: writes a one-line weekly report."""
from datetime import date
from pathlib import Path

out = Path.home() / "reports"
out.mkdir(exist_ok=True)
(out / f"week-{date.today():%G-W%V}.txt").write_text(f"Report for week {date.today():%G-W%V}\n")
print("weekly report ok")
