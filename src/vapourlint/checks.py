"""Checks that need the plugin catalog but do not run the script.

Rules (the names are what ``# vapourlint: ignore[...]`` takes):

- ``unknown-function``: ``core.ns.fn`` where ``ns`` is installed but has no ``fn``;
- ``unknown-argument``: a keyword the function does not declare;
- ``missing-argument``: a required parameter nobody supplies.

A call with ``*args`` or ``**kwargs`` is not checked for missing arguments,
and a function whose signature accepts any keyword is not checked for
unknown ones. A method-form call supplies the function's first parameter.
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass

from .catalog import Catalog
from .manifest import call_sites

IGNORE = re.compile(r"vapourlint:\s*ignore(?:\[([^\]]*)\])?")


@dataclass(frozen=True, order=True)
class Diagnostic:
    line: int
    column: int
    code: str
    message: str


def check(source: str, catalog: Catalog, filename: str = "<script>") -> list[Diagnostic]:
    """Raises SyntaxError for invalid Python."""
    found: list[Diagnostic] = []
    for site in call_sites(source, filename, catalog):
        call = site.call
        where = (call.line, call.column)
        function = catalog.get(call.namespace, call.function)
        if function is None:
            if call.form == "core" and call.namespace in catalog.namespaces:
                found.append(Diagnostic(*where, "unknown-function", f"{call.namespace} has no function {call.function}"))
            continue
        if not function.accepts_any:
            for name in site.keywords:
                if function.param(name) is None:
                    found.append(Diagnostic(*where, "unknown-argument", f"{call.name} has no argument {name!r}"))
        if site.starred:
            continue
        offset = 1 if call.form == "method" else 0
        supplied = {p.name for p in function.params[: offset + site.positional]}
        supplied.update(p.name for name in site.keywords if (p := function.param(name)) is not None)
        missing = [p.python_name for p in function.params if not p.optional and p.name not in supplied]
        if missing:
            found.append(Diagnostic(*where, "missing-argument", f"{call.name} needs {', '.join(missing)}"))
    ignored = _ignored(source)
    return sorted(d for d in found if not _silenced(d, ignored))


def _ignored(source: str) -> dict[int, set[str] | None]:
    """Line -> codes ignored on it (None: every code)."""
    lines: dict[int, set[str] | None] = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and (match := IGNORE.search(token.string)):
            codes = match.group(1)
            lines[token.start[0]] = {c.strip() for c in codes.split(",") if c.strip()} if codes else None
    return lines


def _silenced(diagnostic: Diagnostic, ignored: dict[int, set[str] | None]) -> bool:
    if diagnostic.line not in ignored:
        return False
    codes = ignored[diagnostic.line]
    return codes is None or diagnostic.code in codes
