"""Rules over the graph pass's record (see ``graph``).

- ``graph-error`` (error): VapourSynth refused to build a line.
- ``matrix-unspecified`` (error): frame 0 of a YUV-to-RGB conversion fails because
  no matrix is known; VapourSynth's own "no path between colorspaces".
- ``render-error`` (error): frame 0 of a line's clip fails to render for another reason.
- ``slice-past-end`` (warning): ``clip[a:b]`` with a literal end past the clip's
  length, which VapourSynth clamps silently.
- ``source-stand-in`` (info): a source was replaced by a stand-in, and what that leaves
  unchecked.

A stand-in's undeclared fields are invented, so nothing that may depend on them
is reported as an error: findings that could come from an assumed format, size,
length or matrix are downgraded to info and say which declaration would check them.
"""

from __future__ import annotations

import ast
from typing import Any

from .diagnostics import Diagnostic
from .graph import DEFAULTS, FIELDS, declared_fields

COLORSPACE = "no path between colorspaces"


def graph_diagnostics(source: str, record: dict[str, Any], filename: str = "<script>") -> list[Diagnostic]:
    lines = source.splitlines()
    found: list[Diagnostic] = []
    assumed: set[str] = set()
    for stand_in in (s for s in record["sources"] if s["mode"] == "stand-in" and "Audio" not in s["name"]):
        declared = declared_fields(dict.fromkeys(stand_in["declared"]))
        missing = [f for f in FIELDS if f not in declared]
        assumed.update(missing)
        found.append(_stand_in(stand_in, missing))
    shape_assumed = bool(assumed & {"size", "format", "fps", "frames"})

    for error in record["errors"]:
        line, message = error["line"], error["message"]
        last = message.strip().splitlines()[-1] if message.strip() else error["type"]
        if error["phase"] == "build":
            text = f"VapourSynth refused this line: {last}"
            found.append(_maybe_assumed(line, lines, "graph-error", text, shape_assumed, "format or size"))
        elif COLORSPACE in message:
            text = (
                f"{error['name']}: converting YUV to RGB needs a matrix and this clip has none; "
                "set _Matrix at the source or pass matrix_in"
            )
            found.append(_maybe_assumed(line, lines, "matrix-unspecified", text, "matrix" in assumed, "matrix"))
        else:
            text = f"frame 0 of {error['name']} fails to render: {last}"
            found.append(_maybe_assumed(line, lines, "render-error", text, shape_assumed, "format or size"))

    if "frames" not in assumed:
        found += _slices(source, record, lines, filename)
    if record.get("render_stopped"):
        found.append(Diagnostic(1, 1, "render-error", "rendering stopped at the time budget; later lines were not render-checked", "info"))
    return found


def _stand_in(stand_in: dict[str, Any], missing: list[str]) -> Diagnostic:
    why = "the file does not exist" if not stand_in["exists"] else "real media is opened only with --media"
    text = f"{stand_in['name']} replaced by a stand-in ({why})"
    if missing:
        assumed = {
            "size": f"{DEFAULTS['width']}x{DEFAULTS['height']}",
            "format": DEFAULTS["format"],
            "fps": "/".join(map(str, DEFAULTS["fps"])),
            "frames": f"frames={DEFAULTS['frames']}",
            "matrix": "no matrix",
        }
        text += (
            f"; assumed {', '.join(assumed[f] for f in missing)}. Checks that depend on those were not run; "
            "declare them with # vapourlint: source"
        )
    return Diagnostic(stand_in["line"], 1, "source-stand-in", text, "info")


def _maybe_assumed(line: int, lines: list[str], code: str, text: str, assumed: bool, what: str) -> Diagnostic:
    column = len(lines[line - 1]) - len(lines[line - 1].lstrip()) + 1 if 0 < line <= len(lines) else 1
    if assumed:
        return Diagnostic(line, column, code, f"{text} (may come from a stand-in's assumed {what}; declare it to check)", "info")
    return Diagnostic(line, column, code, text)


def _slices(source: str, record: dict[str, Any], lines: list[str], filename: str) -> list[Diagnostic]:
    """Literal slice ends past the length of the clip the name held before that line."""
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if not (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and isinstance(node.slice, ast.Slice)):
            continue
        upper = node.slice.upper
        if not (isinstance(upper, ast.Constant) and type(upper.value) is int and upper.value > 0):
            continue
        before = [b for b in record["bindings"] if b["name"] == node.value.id and b["line"] < node.lineno]
        if not before or before[-1]["kind"] != "video":
            continue
        frames = before[-1]["frames"]
        if upper.value > frames:
            line = lines[node.lineno - 1]
            column = len(line.encode()[: node.col_offset].decode(errors="replace")) + 1
            found.append(
                Diagnostic(
                    node.lineno,
                    column,
                    "slice-past-end",
                    f"{node.value.id} has {frames} frames, so the slice end {upper.value} is clamped silently",
                    "warning",
                )
            )
    return found
