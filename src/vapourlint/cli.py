"""Command line: ``vapourlint manifest SCRIPT [--json]``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .manifest import Manifest, analyze


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vapourlint", description="A static analyzer for VapourSynth scripts.")
    parser.add_argument("--version", action="version", version=f"vapourlint {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="list a script's imports and plugin calls without running it")
    manifest.add_argument("script", type=Path)
    manifest.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)

    try:
        source = args.script.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"vapourlint: {args.script}: {error}", file=sys.stderr)
        return 2
    try:
        result = analyze(source, str(args.script))
    except SyntaxError as error:
        print(f"{args.script}:{error.lineno}:{error.offset}: syntax error: {error.msg}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_json(str(args.script)), indent=2, ensure_ascii=False))
    else:
        print(_text(str(args.script), result))
    return 0


def _text(script: str, result: Manifest) -> str:
    lines = [script, f"  plugins: {', '.join(result.plugins) or '(none)'}"]
    if result.imports:
        lines.append("  imports: " + ", ".join(f"{i.module} ({i.line})" for i in result.imports))
    if result.calls:
        lines.append("  calls:")
        for call in result.calls:
            where = f"{call.line}:{call.column}"
            strings = "  " + " ".join(json.dumps(s, ensure_ascii=False) for s in call.strings) if call.strings else ""
            lines.append(f"    {where:<8} {call.name}{strings}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
