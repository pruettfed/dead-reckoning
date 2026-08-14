"""Post, toggle, clear, or show the status bar announcement. SPENDS NO PU.

Talks to the database directly, so it needs no API key and works regardless
of ENV — production exposes no HTTP write path for this at all, by design
(see CLAUDE.md). Same shape as scripts/dev_reset.py and scripts/analyze.py.

    cd backend
    .venv/bin/python scripts/status_message.py post "AIS ingest degraded, investigating" --level warning
    .venv/bin/python scripts/status_message.py post "Scheduled maintenance 18:00 UTC" --level info
    .venv/bin/python scripts/status_message.py post "CDSE outage, no new passes" --level critical --title "OUTAGE"
    .venv/bin/python scripts/status_message.py toggle
    .venv/bin/python scripts/status_message.py clear
    .venv/bin/python scripts/status_message.py show

    # against a deployed backend — `railway run` can't reach db.railway.internal;
    # `railway ssh` executes inside the container itself, where it resolves
    railway ssh -- bash -c 'cd /app && python scripts/status_message.py show'
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import status_message  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def _print_status(status: dict) -> None:
    if not status["active"] and status["message"] is None:
        print("no message set")
        return
    state = "ACTIVE" if status["active"] else "inactive"
    title_suffix = f" title={status['title']}" if status.get("title") else ""
    print(f"[{state}] level={status['level']}{title_suffix}")
    print(status["message"] or "(no message)")
    if status["updated_at"]:
        print(f"updated: {status['updated_at']:%Y-%m-%d %H:%M:%S %Z}")


async def _run_post(args: argparse.Namespace) -> int:
    try:
        status_message.validate_post(args.message, args.level, args.title)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    async with SessionLocal() as session:
        result = await status_message.post(session, args.message, args.level, args.title)
        await session.commit()
    _print_status(result)
    return 0


async def _run_toggle(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        result = await status_message.toggle(session)
        await session.commit()
    if result["message"] is None:
        print("no message to toggle — use `post` first", file=sys.stderr)
        return 1
    _print_status(result)
    return 0


async def _run_clear(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        await status_message.clear(session)
        await session.commit()
    print("cleared")
    return 0


async def _run_show(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        result = await status_message.get_status(session)
    _print_status(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    post = subparsers.add_parser("post", help="set the message and level, and activate it")
    post.add_argument("message")
    post.add_argument("--level", choices=status_message.VALID_LEVELS, default="warning")
    post.add_argument(
        "--title",
        default=None,
        help="override the INFO/WARNING/CRITICAL badge text (still colored by --level)",
    )
    post.set_defaults(func=_run_post)

    toggle = subparsers.add_parser("toggle", help="flip active/inactive without losing the text")
    toggle.set_defaults(func=_run_toggle)

    clear = subparsers.add_parser("clear", help="remove the message entirely")
    clear.set_defaults(func=_run_clear)

    show = subparsers.add_parser("show", help="print the current state")
    show.set_defaults(func=_run_show)

    return parser


async def main() -> int:
    args = build_parser().parse_args()
    return await args.func(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
