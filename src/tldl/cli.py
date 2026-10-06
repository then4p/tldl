"""Command line entry point."""

import argparse
import asyncio
import logging
import mimetypes
import os
import sys
from pathlib import Path

from .config import load_config
from .core import Bot, build_engines
from .models import Audio


async def _transcribe_files(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    engines = build_engines(config)
    names = args.engine or [config.default_engine]
    unknown = [n for n in names if n not in engines]
    if unknown:
        print(f"unknown engine(s): {', '.join(unknown)}; available: {', '.join(engines)}", file=sys.stderr)
        return 2
    try:
        for name in names:
            engine = engines[name]
            await engine.ensure_loaded()  # keep model load time out of the timings
            for path in args.files:
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                audio = Audio(data=Path(path).read_bytes(), mime_type=mime, filename=Path(path).name)
                t = await engine.transcribe(audio, args.language)
                rtf = f", RTF {t.elapsed / t.duration:.2f}" if t.duration else ""
                print(f"== {name} | {path} | {t.elapsed:.2f}s{rtf} | lang={t.language}")
                print(t.text, end="\n\n", flush=True)
    finally:
        for engine in engines.values():
            await engine.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tldl")
    parser.add_argument("-c", "--config", default=os.environ.get("TLDL_CONFIG", "config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the bot")
    tr = sub.add_parser("transcribe", help="transcribe local files, e.g. to compare engines")
    tr.add_argument("files", nargs="+")
    tr.add_argument("-e", "--engine", action="append", help="engine name from config (repeatable)")
    tr.add_argument("-l", "--language")
    sub.add_parser("engines", help="list configured engines")
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
    elif args.command == "engines":
        config = load_config(args.config)
        for name, section in config.engines.items():
            marker = "*" if name == config.default_engine else " "
            print(f"{marker} {name}: {section['type']}")


if __name__ == "__main__":
    main()
