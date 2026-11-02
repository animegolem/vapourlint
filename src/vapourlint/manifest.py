"""The static manifest: what a script imports and which plugin functions it calls.

Nothing is executed. The core is recognised in these forms:

- ``vapoursynth.core`` or ``vs.core`` after ``import vapoursynth as vs``;
- ``core`` after ``from vapoursynth import core`` (any alias);
- a name assigned from one of those, or from the legacy ``vs.get_core()``.

Method-form calls such as ``clip.std.Trim(...)`` are reported only when a
plugin catalog is supplied: without it they cannot be told apart from
ordinary attribute calls. With it, ``x.ns.fn(...)`` counts when ``ns.fn`` is
an installed function whose first parameter takes a clip.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .parsing import parse

if TYPE_CHECKING:
    from .catalog import Catalog

VAPOURSYNTH = "vapoursynth"


@dataclass(frozen=True)
class Import:
    module: str
    line: int


@dataclass(frozen=True)
class Call:
    namespace: str
    function: str
    line: int
    column: int  # 1-based, in characters
    strings: list[str] = field(default_factory=list)
    form: Literal["core", "method"] = "core"

    @property
    def name(self) -> str:
        return f"{self.namespace}.{self.function}"


@dataclass(frozen=True)
class CallSite:
    """A call plus what it supplies, for the checks."""

    call: Call
    positional: int
    keywords: tuple[str, ...]
    starred: bool  # *args or **kwargs: what is supplied is unknown
    offset: int  # the AST's col_offset (UTF-8 bytes), to find this call's node again


@dataclass(frozen=True)
class Manifest:
    imports: list[Import]
    calls: list[Call]

    @property
    def plugins(self) -> list[str]:
        return sorted({call.namespace for call in self.calls})

    def to_json(self, script: str) -> dict[str, Any]:
        from . import __version__

        return {
            "vapourlint": __version__,
            "script": script,
            "plugins": self.plugins,
            "imports": [asdict(i) for i in self.imports],
            "calls": [asdict(c) for c in self.calls],
        }


def analyze(source: str, filename: str = "<script>", catalog: Catalog | None = None) -> Manifest:
    """Build the manifest for one script. Raises SyntaxError for invalid Python."""
    visitor = _visit(source, filename, catalog)
    return Manifest(visitor.imports, [site.call for site in visitor.sites])


def call_sites(source: str, filename: str = "<script>", catalog: Catalog | None = None) -> list[CallSite]:
    return _visit(source, filename, catalog).sites


def _visit(source: str, filename: str, catalog: Catalog | None) -> _Visitor:
    visitor = _Visitor(source.splitlines(), catalog)
    visitor.visit(parse(source, filename))
    return visitor


class _Visitor(ast.NodeVisitor):
    def __init__(self, lines: list[str], catalog: Catalog | None) -> None:
        self.lines = lines
        self.catalog = catalog
        self.modules: set[str] = set()  # names bound to the vapoursynth module
        self.cores: set[str] = set()  # names bound to the core
        self.imports: list[Import] = []
        self.sites: list[CallSite] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(Import(alias.name, node.lineno))
            if alias.name == VAPOURSYNTH:
                self.modules.add(alias.asname or VAPOURSYNTH)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        self.imports.append(Import(module, node.lineno))
        if module == VAPOURSYNTH:
            self.cores.update(a.asname or a.name for a in node.names if a.name == "core")

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._is_core(node.value):
            self.cores.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and self._is_core(node.value) and isinstance(node.target, ast.Name):
            self.cores.add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            namespace, function, receiver = func.value.attr, func.attr, func.value.value
            if self._is_core(receiver):
                self._record(node, namespace, function, "core")
            elif self.catalog is not None and not self._is_module(receiver):
                found = self.catalog.get(namespace, function)
                if found is not None and found.on_clips:
                    self._record(node, namespace, function, "method")
        self.generic_visit(node)

    def _record(self, node: ast.Call, namespace: str, function: str, form: Literal["core", "method"]) -> None:
        arguments = [*node.args, *(k.value for k in node.keywords)]
        strings = [a.value for a in arguments if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        call = Call(namespace, function, node.lineno, self._column(node), strings, form)
        starred = any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords)
        keywords = tuple(k.arg for k in node.keywords if k.arg is not None)
        positional = sum(1 for a in node.args if not isinstance(a, ast.Starred))
        self.sites.append(CallSite(call, positional, keywords, starred, node.col_offset))

    def _is_core(self, expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in self.cores
        if isinstance(expr, ast.Attribute) and expr.attr == "core":
            return self._is_module(expr.value)
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get_core":
            return self._is_module(expr.func.value)
        return False

    def _is_module(self, expr: ast.expr) -> bool:
        return isinstance(expr, ast.Name) and expr.id in self.modules

    def _column(self, node: ast.expr) -> int:
        # The AST counts UTF-8 bytes; editors and people count characters.
        line = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
        return len(line.encode()[: node.col_offset].decode(errors="replace")) + 1
