"""Command line entry point."""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from .audio import SAMPLE_RATE, decode
from .config import load_config
from .core import Bot
from .models import Audio
from .transcriber import Transcriber


async def _transcribe_files(args: argparse.Namespace) -> int:
    """Both passes on local files, with timings."""
    config = load_config(args.config)
    transcriber = Transcriber(threads=config.threads)
    await transcriber.load()  # keep model load time out of the timings
    for path in args.files:
        samples = await decode(Audio(data=Path(path).read_bytes(), filename=Path(path).name))
        seconds = len(samples) / SAMPLE_RATE
        for name, run in (("parakeet", transcriber.fast), ("whisper", transcriber.better)):
            start = time.perf_counter()
            text = await run(samples)
            elapsed = time.perf_counter() - start
            print(f"== {name} | {path} | {seconds:.0f}s audio in {elapsed:.1f}s")
            print(text, end="\n\n", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tldl")
    parser.add_argument("-c", "--config", default=os.environ.get("TLDL_CONFIG", "config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the bot")
    tr = sub.add_parser("transcribe", help="transcribe local files with both passes")
    tr.add_argument("files", nargs="+")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if args.command == "run":
        try:
            asyncio.run(Bot(load_config(args.config)).run())
        except KeyboardInterrupt:
            pass
    elif args.command == "transcribe":
        sys.exit(asyncio.run(_transcribe_files(args)))


if __name__ == "__main__":
    main()
