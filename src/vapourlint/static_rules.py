"""Rules that read only the script's text: no VapourSynth, no catalog, nothing run.

- ``callback-signature`` (error): ``std.ModifyFrame`` calls its selector with the
  keywords ``n`` and ``f``, and ``std.FrameEval`` calls its eval with ``n`` (plus ``f``
  when ``prop_src`` is given). A callback that cannot take them fails only when a
  frame is rendered.
- ``late-global`` (warning): a frame callback reads a module-level name that the
  script assigns again after the call. Frames are rendered after the script has
  finished, so every frame sees the later value.
- ``range-literal`` (warning): ``_Range`` set to a bare 0 or 1. ``_Range`` is numbered
  the other way from the deprecated ``_ColorRange`` (0 = limited), so write
  ``vs.RANGE_LIMITED`` or ``vs.RANGE_FULL``.
- ``deprecated-prop`` (info): ``_ColorRange``, which VapourSynth translates to ``_Range``.
- ``replace-ranges-convention`` (warning): ``replace_ranges`` (or ``rfs``) without
  ``exclusive=``. Its default is inclusive, and the default is a process-wide switch
  any imported script can flip.
"""

from __future__ import annotations

import ast
import builtins

from .diagnostics import Diagnostic
from .manifest import call_sites

# std function -> (callback keyword, its positional index in core form)
CALLBACKS = {"ModifyFrame": ("selector", 2), "FrameEval": ("eval", 1), "Lut": ("function", 4), "Lut2": ("function", 5)}
SIGNATURES = {"ModifyFrame": ("n", "f"), "FrameEval": ("n",)}
RANGE_HELPERS = {"replace_ranges", "rfs"}
BUILTINS = set(dir(builtins))


def static_diagnostics(source: str, filename: str = "<script>") -> list[Diagnostic]:
    """Raises SyntaxError for invalid Python."""
    tree = ast.parse(source, filename=filename)
    rules = _Rules(source.splitlines(), tree, {(s.call.line, s.offset) for s in call_sites(source, filename)})
    rules.visit(tree)
    return rules.found


class _Rules(ast.NodeVisitor):
    def __init__(self, lines: list[str], tree: ast.Module, core_calls: set[tuple[int, int]]) -> None:
        self.lines = lines
        self.core_calls = core_calls
        self.defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        self.assigned = _module_assignments(tree)
        self.found: list[Diagnostic] = []

    def add(self, node: ast.AST, code: str, message: str, severity: str = "error") -> None:
        self.found.append(Diagnostic(node.lineno, self._column(node), code, message, severity))  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) and func.value.attr == "std":
            if func.attr in CALLBACKS:
                self._callback(node, func.attr)
            if func.attr in {"SetFrameProps", "SetFrameProp"}:
                self._props_call(node)
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in RANGE_HELPERS and not any(k.arg == "exclusive" for k in node.keywords):
            self.add(
                node,
                "replace-ranges-convention",
                f"{name} defaults to inclusive ranges, and that default can be switched process-wide; "
                "pass exclusive= explicitly",
                "warning",
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
            for target in node.targets:
                key = (
                    target.slice.value
                    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if key == "_Range":
                    self._range_literal(node, node.value.value)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == "_ColorRange":
            self._deprecated(node)

    def _props_call(self, node: ast.Call) -> None:
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        if "_ColorRange" in keywords:
            self._deprecated(node)
        literal = keywords.get("_Range")
        if keywords.get("prop") is not None and isinstance(keywords["prop"], ast.Constant):
            if keywords["prop"].value == "_Range":
                literal = keywords.get("intval")
        if isinstance(literal, ast.Constant) and type(literal.value) is int:
            self._range_literal(node, literal.value)

    def _range_literal(self, node: ast.AST, value: int) -> None:
        meaning = {0: "limited", 1: "full"}.get(value, "not a valid range")
        constant = {0: "vs.RANGE_LIMITED", 1: "vs.RANGE_FULL"}.get(value)
        hint = f"; write {constant}" if constant else ""
        self.add(
            node,
            "range-literal",
            f"_Range={value} means {meaning}: _Range is numbered the other way from _ColorRange{hint}",
            "warning",
        )

    def _deprecated(self, node: ast.AST) -> None:
        self.add(node, "deprecated-prop", "_ColorRange is deprecated; VapourSynth translates it to _Range", "info")

    def _callback(self, node: ast.Call, function: str) -> None:
        keyword, index = CALLBACKS[function]
        method = (node.lineno, node.col_offset) not in self.core_calls
        position = index - 1 if method else index
        given = {k.arg: k.value for k in node.keywords if k.arg}
        callback = given.get(keyword)
        if callback is None and 0 <= position < len(node.args):
            callback = node.args[position]
        target = callback if isinstance(callback, ast.Lambda) else None
        if isinstance(callback, ast.Name):
            target = self.defs.get(callback.id)
        if target is None:
            return
        if function in SIGNATURES:
            prop_src = "prop_src" in given or len(node.args) > (1 if method else 2)
            required = SIGNATURES[function] + (("f",) if function == "FrameEval" and prop_src else ())
            self._signature(node, function, keyword, target, required)
        self._late_globals(node, target)

    def _signature(self, node: ast.Call, function: str, keyword: str, target, required: tuple[str, ...]) -> None:
        args = target.args
        takes_any = args.kwarg is not None
        keywordable = [a.arg for a in args.args + args.kwonlyargs]
        missing = [name for name in required if name not in keywordable and not takes_any]
        # Defaults belong to the last positional parameters, positional-only ones included.
        positional = args.posonlyargs + args.args
        without_default = [a.arg for a in positional[: len(positional) - len(args.defaults)]]
        without_default += [a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None]
        unexpected = [name for name in without_default if name not in required]
        if missing or unexpected:
            takes = ", ".join(a.arg for a in args.posonlyargs + args.args + args.kwonlyargs)
            self.add(
                node,
                "callback-signature",
                f"std.{function} calls {keyword} with keywords {', '.join(required)}; this callback takes ({takes})",
            )

    def _late_globals(self, node: ast.Call, target) -> None:
        args = target.args
        own = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        own |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
        body = target.body if isinstance(target.body, list) else [target.body]
        names = [n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Name)]
        own |= {n.id for n in names if isinstance(n.ctx, ast.Store)}
        reads = sorted({n.id for n in names if isinstance(n.ctx, ast.Load)} - own - BUILTINS)
        for name in reads:
            later = [line for line in self.assigned.get(name, []) if line > node.lineno]
            if later:
                self.add(
                    node,
                    "late-global",
                    f"the callback reads {name}, which line {later[-1]} assigns after this call; "
                    f"frames are rendered after the script finishes, so every frame sees line {later[-1]}'s value",
                    "warning",
                )

    def _column(self, node: ast.AST) -> int:
        line = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
        return len(line.encode()[: node.col_offset].decode(errors="replace")) + 1


def _module_assignments(tree: ast.Module) -> dict[str, list[int]]:
    """Lines where each module-level name is bound, outside function and class bodies."""
    lines: dict[str, list[int]] = {}

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                lines.setdefault(child.name, []).append(child.lineno)
                continue
            if isinstance(child, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
                continue  # their names are their own scope
            if isinstance(child, ast.Import | ast.ImportFrom):
                for alias in child.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    lines.setdefault(bound, []).append(child.lineno)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                lines.setdefault(child.id, []).append(child.lineno)
            walk(child)

    walk(tree)
    return {name: sorted(found) for name, found in lines.items()}
