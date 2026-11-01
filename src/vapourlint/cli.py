"""Command line.

- ``vapourlint manifest SCRIPT``: imports and plugin calls, without running anything.
- ``vapourlint check SCRIPT``: catalog-backed checks; exit 0 clean, 1 findings.
- ``vapourlint catalog``: the installed plugins, as VapourLint sees them.

Exit status 2 means VapourLint could not do its job (unreadable file,
invalid Python, no catalog), never that the script has problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .catalog import Catalog, CatalogUnavailable, discover_data
from .checks import check
from .manifest import Manifest, analyze

FAILED = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vapourlint", description="A static analyzer for VapourSynth scripts.")
    parser.add_argument("--version", action="version", version=f"vapourlint {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    manifest = commands.add_parser("manifest", help="list a script's imports and plugin calls without running it")
    manifest.add_argument("script", type=Path)
    manifest.add_argument("--json", action="store_true", help="print JSON instead of text")

    checker = commands.add_parser("check", help="check plugin calls against the installed catalog")
    checker.add_argument("script", type=Path)
    checker.add_argument("--json", action="store_true", help="print JSON instead of text")
    _catalog_options(checker)

    catalog = commands.add_parser("catalog", help="show the plugin catalog VapourLint would use")
    catalog.add_argument("--json", action="store_true", help="print the full catalog as JSON")
    _catalog_options(catalog)

    args = parser.parse_args(argv)
    try:
        match args.command:
            case "manifest":
                return _manifest(args)
            case "check":
                return _check(args)
            case _:
                return _catalog(args)
    except _Failure as failure:
        print(f"vapourlint: {failure}", file=sys.stderr)
        return FAILED


class _Failure(Exception):
    pass


def _catalog_options(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--python", help="interpreter whose VapourSynth to probe")
    source.add_argument("--catalog", type=Path, help="read a catalog saved with `vapourlint catalog --json`")


def _load_catalog(args: argparse.Namespace) -> tuple[Catalog, dict]:
    if args.catalog is not None:
        try:
            data = json.loads(args.catalog.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise _Failure(f"{args.catalog}: {error}") from None
        return Catalog.from_json(data), data
    try:
        data = discover_data(args.python)
    except CatalogUnavailable as error:
        raise _Failure(str(error)) from None
    return Catalog.from_json(data), data


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _Failure(f"{path}: {error}") from None


def _syntax(path: Path, error: SyntaxError) -> _Failure:
    return _Failure(f"{path}:{error.lineno}:{error.offset}: syntax error: {error.msg}")


def _manifest(args: argparse.Namespace) -> int:
    source = _read(args.script)
    try:
        result = analyze(source, str(args.script))
    except SyntaxError as error:
        raise _syntax(args.script, error) from None
    if args.json:
        print(json.dumps(result.to_json(str(args.script)), indent=2, ensure_ascii=False))
    else:
        print(_manifest_text(str(args.script), result))
    return 0


def _check(args: argparse.Namespace) -> int:
    source = _read(args.script)
    catalog, _ = _load_catalog(args)
    try:
        diagnostics = check(source, catalog, str(args.script))
    except SyntaxError as error:
        raise _syntax(args.script, error) from None
    if args.json:
        report = {
            "vapourlint": __version__,
            "script": str(args.script),
            "vapoursynth": catalog.vapoursynth,
            "diagnostics": [vars(d) for d in diagnostics],
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for d in diagnostics:
            print(f"{args.script}:{d.line}:{d.column}: {d.code}: {d.message}")
    return 1 if diagnostics else 0


def _catalog(args: argparse.Namespace) -> int:
    catalog, data = _load_catalog(args)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    version = f"VapourSynth R{catalog.vapoursynth}" if catalog.vapoursynth else "VapourSynth"
    print(f"{version} via {catalog.python or 'a saved catalog'}")
    print(f"  {len(catalog.namespaces)} plugins, {len(catalog.functions)} functions")
    print("  " + ", ".join(sorted(catalog.namespaces)))
    return 0


def _manifest_text(script: str, result: Manifest) -> str:
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
