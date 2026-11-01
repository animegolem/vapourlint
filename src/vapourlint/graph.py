"""The graph pass: build the script's graph without rendering it, and record each line's clips.

**This runs the script's Python.** It is opt-in (``vapourlint graph``, ``check --graph``);
the other commands never execute anything. It runs in a separate interpreter, like
the catalog probe, one top-level statement at a time:

- after each statement, every name newly bound to a clip is recorded with that
  statement's line: format, size, frame count, frame rate;
- a statement that raises stops the pass, and the error is recorded at the
  innermost line of the script it passed through;
- then, unless rendering is turned off, frame 0 of each recorded clip is requested
  in order, so render-time failures land on the line that introduced them.

Source filters are replaced by stand-ins (``std.BlankClip``) unless ``media`` is set,
because opening real media can take long and some source filters write index
files beside it. A stand-in's format comes from a ``# vapourlint: source ...``
comment on the call's line, or on a line of its own as a default for every source.
Fields nobody declared are assumed, and the rules never report a finding that
depends on an assumed field.

Limits: a compound statement (``if``, ``for``, ``with``) records its clips at its
first line; clips held only inside lists or dicts are not recorded.
"""

from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import tempfile
import tokenize
from pathlib import Path
from typing import Any

from .manifest import call_sites

TIMEOUT = 60.0
RENDER_BUDGET = 30.0
SOURCE_NAMESPACES = {"bs", "lsmas", "ffms2", "dgdecnv", "d2v", "imwri"}
PRAGMA = re.compile(r"vapourlint:\s*source\b(.*)")
MATRICES = {"rgb": 0, "709": 1, "bt709": 1, "fcc": 4, "470bg": 5, "601": 6, "170m": 6, "240m": 7,
            "ycgco": 8, "2020": 9, "2020ncl": 9, "2020cl": 10}  # fmt: skip
RANGES = {"limited": 0, "full": 1}  # R81 numbering
DEFAULTS = {"width": 1920, "height": 1080, "format": "YUV420P8", "fps": [24000, 1001], "frames": 10000}
FIELDS = ("size", "format", "fps", "frames", "matrix")


class GraphUnavailable(Exception):
    """The graph pass could not run at all; the message says why."""


def parse_pragma(text: str) -> dict[str, Any]:
    """``1920x1080 yuv420p10 24000/1001 frames=1200 matrix=709 range=limited`` -> declared fields."""
    declared: dict[str, Any] = {}
    for token in text.split():
        key, _, value = token.partition("=")
        if size := re.fullmatch(r"(\d+)x(\d+)", token):
            declared["width"], declared["height"] = int(size[1]), int(size[2])
        elif fps := re.fullmatch(r"(\d+)/(\d+)", token):
            declared["fps"] = [int(fps[1]), int(fps[2])]
        elif value and key == "frames" and value.isdigit():
            declared["frames"] = int(value)
        elif value and key == "fps" and (fps := re.fullmatch(r"(\d+)/(\d+)", value)):
            declared["fps"] = [int(fps[1]), int(fps[2])]
        elif value and key == "matrix":
            # Names first ("709" is BT.709, not code 709); bare numbers only as VapourSynth's codes 0-14.
            code = int(value) if value.isdigit() and int(value) <= 14 else None
            declared["matrix"] = MATRICES.get(value.lower(), code)
        elif value and key == "range" and value.lower() in RANGES:
            declared["range"] = RANGES[value.lower()]
        elif value and key == "format":
            declared["format"] = value.upper()
        elif not value and re.fullmatch(r"(?i)(yuv\d{3}p|gray|rgb)(\d+|s|h)", token):
            declared["format"] = token.upper()  # YUV420P8, YUV444PS, GRAY16, RGBH, ...
    return {k: v for k, v in declared.items() if v is not None}


def declared_fields(declared: dict[str, Any]) -> set[str]:
    fields = {"size"} if "width" in declared else set()
    return fields | {f for f in ("format", "fps", "frames", "matrix") if f in declared}


def source_plan(source: str, filename: str = "<script>") -> dict[str, dict[str, Any]]:
    """Which calls the probe replaces, keyed "line:offset", with their declared formats."""
    pragmas: dict[int, dict[str, Any]] = {}
    code_lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and (match := PRAGMA.search(token.string)):
            pragmas[token.start[0]] = parse_pragma(match.group(1))
        elif token.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            code_lines.add(token.start[0])
    default: dict[str, Any] = {}
    for line, declared in sorted(pragmas.items()):
        if line not in code_lines:
            default.update(declared)
    plan = {}
    for site in call_sites(source, filename):
        call = site.call
        if call.namespace in SOURCE_NAMESPACES and ("Source" in call.function or call.name == "imwri.Read"):
            declared = {**default, **pragmas.get(call.line, {})}
            plan[f"{call.line}:{site.offset}"] = {"name": call.name, "line": call.line, "declared": declared}
    return plan


def run(
    script: Path,
    python: str,
    *,
    media: bool = False,
    render: bool = True,
    timeout: float = TIMEOUT,
    render_budget: float = RENDER_BUDGET,
) -> dict[str, Any]:
    """Run the graph probe on ``script`` with ``python``. Raises GraphUnavailable or SyntaxError."""
    script = script.resolve()
    source = script.read_text(encoding="utf-8")
    config = {
        "script": str(script),
        "sources": source_plan(source, str(script)),
        "defaults": DEFAULTS,
        "media": media,
        "render": render,
        "render_budget": render_budget,
    }
    with tempfile.TemporaryDirectory(prefix="vapourlint-graph-") as directory:
        config_path, result_path = Path(directory) / "config.json", Path(directory) / "graph.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        try:
            process = subprocess.Popen(
                [python, "-c", PROBE, str(config_path), str(result_path)],
                cwd=script.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,  # its own process group, so a timeout takes all of it
            )
        except FileNotFoundError:
            raise GraphUnavailable(f"{python}: not found") from None
        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise GraphUnavailable(f"the graph pass did not finish within {timeout:g} s") from None
        if process.returncode != 0 or not result_path.exists():
            last = stderr.decode(errors="replace").strip().splitlines()
            raise GraphUnavailable(f"{python}: {last[-1] if last else f'exit status {process.returncode}'}")
        return json.loads(result_path.read_text(encoding="utf-8"))


# Runs inside the probed interpreter: argv = config path, result path.
PROBE = r'''
import ast, json, os, sys, time, traceback, __future__
import vapoursynth as vs

config = json.load(open(sys.argv[1], encoding="utf-8"))
script = config["script"]
sources, defaults = config["sources"], config["defaults"]
result = {"vapoursynth": vs.core.core_version.release_major, "bindings": [], "errors": [],
          "sources": [], "outputs": [], "rendered": 0, "render_stopped": False}

def describe(node):
    if isinstance(node, vs.AudioNode):
        return {"kind": "audio", "sample_rate": node.sample_rate, "channels": node.num_channels,
                "samples": node.num_samples}
    fmt = node.format
    return {"kind": "video", "format": fmt.name if fmt else None,
            "color_family": fmt.color_family.name if fmt else None, "bits": fmt.bits_per_sample if fmt else None,
            "width": node.width, "height": node.height, "frames": node.num_frames,
            "fps": [node.fps.numerator, node.fps.denominator]}

def user_line(error, fallback):
    lines = [f.lineno for f in traceback.extract_tb(error.__traceback__) if f.filename == script]
    return lines[-1] if lines else fallback

def source(key, function, *args, **kwargs):
    plan = sources[key]
    path = next((v for k, v in kwargs.items() if k in ("source", "path", "filename")), None)
    if path is None and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = args[0]
    path = os.fsdecode(path) if path is not None else None
    exists = path is not None and os.path.isfile(path)
    record = {"key": key, "name": plan["name"], "line": plan["line"], "path": path, "exists": exists,
              "declared": sorted(plan["declared"])}
    if config["media"] and exists:
        record["mode"] = "media"
        result["sources"].append(record)
        return function(*args, **kwargs)
    record["mode"] = "stand-in"
    result["sources"].append(record)
    if "Audio" in plan["name"]:
        return vs.core.std.BlankAudio(keep=True)
    d = {**defaults, **plan["declared"]}
    fmt = getattr(vs, d["format"], None)
    if not isinstance(fmt, int):
        raise ValueError(f"vapourlint: unknown format {d['format']!r} in a source comment")
    clip = vs.core.std.BlankClip(width=d["width"], height=d["height"], format=fmt,
                                 length=d["frames"], fpsnum=d["fps"][0], fpsden=d["fps"][1], keep=True)
    props = {}
    if "matrix" in d:
        props["_Matrix"] = d["matrix"]
    if "range" in d:
        props["_Range"] = d["range"]
    return clip.std.SetFrameProps(**props) if props else clip

class Wrap(ast.NodeTransformer):
    def visit_Call(self, node):
        self.generic_visit(node)
        key = f"{node.lineno}:{node.col_offset}"
        if key not in sources:
            return node
        call = ast.Call(func=ast.Name(id="__vapourlint_source__", ctx=ast.Load()),
                        args=[ast.Constant(key), node.func, *node.args], keywords=node.keywords)
        return ast.copy_location(call, node)

tree = ast.parse(open(script, encoding="utf-8").read(), script)
flags = 0
for stmt in tree.body:
    if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
        for alias in stmt.names:
            flags |= getattr(__future__, alias.name).compiler_flag
tree = ast.fix_missing_locations(Wrap().visit(tree))

os.chdir(os.path.dirname(script))
sys.path.insert(0, os.path.dirname(script))
namespace = {"__name__": "__vapoursynth__", "__file__": script, "__vapourlint_source__": source}
seen, nodes = {}, []  # nodes[i] is the clip result["bindings"][i] describes
for stmt in tree.body:
    code = compile(ast.Module(body=[stmt], type_ignores=[]), script, "exec", flags=flags, dont_inherit=True)
    try:
        exec(code, namespace)
    except BaseException as error:
        result["errors"].append({"phase": "build", "line": user_line(error, stmt.lineno),
                                 "type": type(error).__name__, "message": str(error).strip()[:2000]})
        break
    for name, value in list(namespace.items()):
        if isinstance(value, (vs.VideoNode, vs.AudioNode)) and seen.get(name) is not value:
            seen[name] = value
            nodes.append(value)
            result["bindings"].append({"line": stmt.lineno, "end_line": stmt.end_lineno, "name": name,
                                       **describe(value)})

for index, output in sorted(vs.get_outputs().items()):
    node = output.clip if isinstance(output, vs.VideoOutputTuple) else output
    result["outputs"].append({"index": index, **describe(node)})

if config["render"]:
    # Frame 0 of each recorded clip, in script order: a failure lands on the line that introduced it,
    # and the same failure seen again downstream is not repeated.
    started, messages = time.monotonic(), set()
    for binding, node in zip(result["bindings"], nodes):
        if binding["kind"] != "video":
            continue
        if time.monotonic() - started > config["render_budget"]:
            result["render_stopped"] = True
            break
        try:
            node.get_frame(0)
            result["rendered"] += 1
        except BaseException as error:
            message = str(error).strip()
            if message not in messages:
                messages.add(message)
                result["errors"].append({"phase": "render", "line": binding["line"], "name": binding["name"],
                                         "type": type(error).__name__, "message": message[:2000]})

with open(sys.argv[2], "w", encoding="utf-8") as out:
    json.dump(result, out)
'''
