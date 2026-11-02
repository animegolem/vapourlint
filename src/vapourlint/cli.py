"""Command line.

- ``vapourlint manifest SCRIPT``: imports and plugin calls, without running anything.
- ``vapourlint check SCRIPT``: static and catalog-backed checks; exit 0 clean, 1 errors or warnings.
- ``vapourlint catalog``: the installed plugins, as VapourLint sees them.
- ``vapourlint graph SCRIPT``: build the script's graph and show each line's clips.
  This and ``check --graph`` run the script's Python; nothing else does.

Exit status 2 means VapourLint could not do its job (unreadable file,
invalid Python, no catalog), never that the script has problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import graph
from .catalog import Catalog, CatalogUnavailable, discover_data
from .checks import check
from .graph_rules import graph_diagnostics
from .manifest import Manifest, analyze
from .parsing import parse

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
    checker.add_argument("--graph", action="store_true", help="also build the graph (runs the script's Python)")
    _catalog_options(checker)
    _graph_options(checker)

    grapher = commands.add_parser("graph", help="build the graph and show each line's clips (runs the script's Python)")
    grapher.add_argument("script", type=Path)
    grapher.add_argument("--json", action="store_true", help="print the raw record as JSON")
    grapher.add_argument("--python", help="interpreter whose VapourSynth to use")
    _graph_options(grapher)

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
            case "graph":
                return _graph(args)
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


def _graph_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--media", action="store_true", help="open real source files (source filters may write index files)")
    parser.add_argument("--no-render", action="store_true", help="build only; do not render frame 0 of each clip")
    parser.add_argument("--timeout", type=float, default=graph.TIMEOUT, help="seconds before the graph pass is stopped")


def _graph_python(args: argparse.Namespace, catalog: Catalog | None) -> str:
    if args.python:
        return args.python
    if catalog is not None and catalog.python and Path(catalog.python).exists():
        return catalog.python
    try:
        return discover_data()["python"]
    except CatalogUnavailable as error:
        raise _Failure(f"the graph pass needs VapourSynth: {error}") from None


def _graph_record(args: argparse.Namespace, python: str) -> dict:
    try:
        return graph.run(args.script, python, media=args.media, render=not args.no_render, timeout=args.timeout)
    except graph.GraphUnavailable as error:
        raise _Failure(str(error)) from None


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
    catalog: Catalog | None = None
    if args.catalog is not None or args.python:
        catalog, _ = _load_catalog(args)  # asked for explicitly: failing is an error
    else:
        try:
            catalog = Catalog.from_json(discover_data())
        except CatalogUnavailable as error:
            print(f"vapourlint: only static rules ran ({error})", file=sys.stderr)
    try:
        extra = []
        if args.graph:
            record = _graph_record(args, _graph_python(args, catalog))
            extra = graph_diagnostics(source, record, str(args.script))
        diagnostics = check(source, catalog, str(args.script), extra)
    except SyntaxError as error:
        raise _syntax(args.script, error) from None
    if args.json:
        report = {
            "vapourlint": __version__,
            "script": str(args.script),
            "vapoursynth": catalog.vapoursynth if catalog else None,
            "diagnostics": [vars(d) for d in diagnostics],
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for d in diagnostics:
            print(f"{args.script}:{d.line}:{d.column}: {d.severity}: {d.code}: {d.message}")
    return 1 if any(d.counts for d in diagnostics) else 0


def _graph(args: argparse.Namespace) -> int:
    source = _read(args.script)
    try:
        parse(source, str(args.script))
    except SyntaxError as error:
        raise _syntax(args.script, error) from None
    record = _graph_record(args, _graph_python(args, None))
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return 0
    print(f"{args.script}  (VapourSynth R{record['vapoursynth']})")
    print("  line  name             clip")
    for b in record["bindings"]:
        print(f"  {b['line']:>4}  {b['name']:<16} {_clip_text(b)}")
    for s in record["sources"]:
        how = "real media" if s["mode"] == "media" else "stand-in" + ("" if s["exists"] else ", file not found")
        declared = f"; declared {', '.join(s['declared'])}" if s["declared"] else "; nothing declared"
        print(f"  {s['line']:>4}  source {s['name']}: {how}{declared if s['mode'] != 'media' else ''}")
    for o in record["outputs"]:
        print(f"        output {o['index']}: {_clip_text(o)}")
    for e in record["errors"]:
        print(f"  {e['line']:>4}  {e['phase']} error: {e['message'].strip().splitlines()[-1] if e['message'].strip() else e['type']}")
    if record["render_stopped"]:
        print("        rendering stopped at the time budget")
    return 0


def _clip_text(clip: dict) -> str:
    if clip["kind"] == "audio":
        return f"audio {clip['channels']} ch {clip['sample_rate']} Hz, {clip['samples']} samples"
    fps = f"{clip['fps'][0]}/{clip['fps'][1]}" if clip["fps"][1] else "variable fps"
    return f"{clip['width']}x{clip['height']} {clip['format'] or 'variable format'} {fps}, {clip['frames']} frames"


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
