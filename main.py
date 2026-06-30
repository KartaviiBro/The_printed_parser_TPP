# main.py
"""CLI entrypoint for the 3D-model scraper.

Examples
--------
    python main.py --list
    python main.py printables --limit 30
    python main.py printables --headful      # show the browser (best vs Cloudflare)
    python main.py --all --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from db.database import init_db
from scraper import SCRAPERS
from scraper.logging_setup import setup_logging

log = logging.getLogger("main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape 3D models from print platforms.")
    parser.add_argument(
        "platform",
        nargs="?",
        choices=sorted(SCRAPERS),
        help="Platform to scrape (omit with --all to scrape every platform).",
    )
    parser.add_argument("--all", action="store_true", help="Scrape every registered platform.")
    parser.add_argument("--list", action="store_true", help="List registered platforms and exit.")
    parser.add_argument("--limit", type=int, default=100, help="Max models per platform.")
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run with a visible browser (recommended for solving Cloudflare).",
    )
    parser.add_argument("--debug", action="store_true", help="Verbose (DEBUG) logging.")
    return parser


async def run_platform(name: str, *, limit: int, headless: bool) -> None:
    scraper = SCRAPERS[name](headless=headless)
    await scraper.run(limit=limit)


async def main_async(args: argparse.Namespace) -> None:
    init_db()
    headless = not args.headful

    if args.all:
        targets = list(SCRAPERS)
    elif args.platform:
        targets = [args.platform]
    else:
        log.error("Specify a platform, or use --all / --list. See --help.")
        return

    for name in targets:
        await run_platform(name, limit=args.limit, headless=headless)


def main() -> None:
    args = build_parser().parse_args()
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.list:
        print("Registered platforms:")
        for name in sorted(SCRAPERS):
            print(f"  - {name}")
        return

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
