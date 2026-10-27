"""Entry point: `python -m cron_agent [repl|server|eval]`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cron-agent",
                                     description="Schedule cron jobs from plain English, safely.")
    parser.add_argument("--provider", choices=["hybrid", "rules", "ollama", "anthropic", "openai"],
                        help="planner that reads your request (default: hybrid = offline rules, "
                             "plus a local or Claude model for requests the rules can't read)")
    parser.add_argument("--backend", choices=["auto", "system", "file"],
                        help="system = real crontab, file = a plain file (default: auto)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("repl", help="interactive prompt (default)")
    sub.add_parser("server", help="run the MCP tool server on stdio")
    ev = sub.add_parser("eval", help="score a planner on the labelled prompt set")
    ev.add_argument("--set", dest="path", help="path to a prompts .jsonl file")
    ev.add_argument("--out", help="folder for the results files")
    dmn = sub.add_parser("daemon", help="run scheduled jobs from the file crontab (cron for Windows)")
    dmn.add_argument("--once", action="store_true", help="run this minute's jobs, wait, then exit")
    dmn.add_argument("--force", action="store_true", help="run even where a system crontab exists")
    bench = sub.add_parser("bench", help="time request -> installed job (throwaway crontab)")
    bench.add_argument("--reps", type=int, default=5)
    bench.add_argument("--out", help="folder for the results files")
    args = parser.parse_args(argv)

    if args.backend:
        os.environ["CRON_AGENT_BACKEND"] = args.backend
    if args.provider:
        os.environ["CRON_AGENT_PROVIDER"] = args.provider

    if args.cmd == "server":
        from .server import main as serve

        serve()
        return
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "eval":
        from .evaluate import main as evaluate

        evaluate(args.path, args.out)
        return
    if args.cmd == "daemon":
        from .daemon import main as run_daemon

        run_daemon(args.once, args.force)
        return
    if args.cmd == "bench":
        from .benchmark import main as benchmark

        benchmark(args.reps, args.out)
        return
    from .repl import run

    asyncio.run(run())


if __name__ == "__main__":
    main()
