"""The static manifest: what a script imports and which plugin functions it calls.

Nothing is executed. The core is recognised in these forms:

- ``vapoursynth.core`` or ``vs.core`` after ``import vapoursynth as vs``;
- ``core`` after ``from vapoursynth import core`` (any alias);
- a name assigned from one of those, or from the legacy ``vs.get_core()``.

Method-form calls such as ``clip.std.Trim(...)`` are not reported yet: telling
them apart from ordinary attribute calls needs the installed plugin catalog.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import Any

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

    @property
    def name(self) -> str:
        return f"{self.namespace}.{self.function}"


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


def analyze(source: str, filename: str = "<script>") -> Manifest:
    """Build the manifest for one script. Raises SyntaxError for invalid Python."""
    tree = ast.parse(source, filename=filename)
    visitor = _Visitor(source.splitlines())
    visitor.visit(tree)
    return Manifest(visitor.imports, visitor.calls)


class _Visitor(ast.NodeVisitor):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.modules: set[str] = set()  # names bound to the vapoursynth module
        self.cores: set[str] = set()  # names bound to the core
        self.imports: list[Import] = []
        self.calls: list[Call] = []

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
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and self._is_core(func.value.value)
        ):
            arguments = [*node.args, *(k.value for k in node.keywords)]
            strings = [a.value for a in arguments if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            self.calls.append(Call(func.value.attr, func.attr, node.lineno, self._column(node), strings))
        self.generic_visit(node)

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
