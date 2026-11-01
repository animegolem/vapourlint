"""``vapourlint check``: static rules always, catalog rules when a catalog exists.

Catalog rules (the names are what ``# vapourlint: ignore[...]`` takes):

- ``unknown-function``: ``core.ns.fn`` where ``ns`` is installed but has no ``fn``;
- ``unknown-argument``: a keyword the function does not declare;
- ``missing-argument``: a required parameter nobody supplies.

A call with ``*args`` or ``**kwargs`` is not checked for missing arguments,
and a function whose signature accepts any keyword is not checked for
unknown ones. A method-form call supplies the function's first parameter.
The static rules are in ``static_rules``; neither layer runs the script.
"""

from __future__ import annotations

from collections.abc import Iterable

from .catalog import Catalog
from .diagnostics import Diagnostic, finalize
from .manifest import call_sites
from .static_rules import static_diagnostics


def check(
    source: str,
    catalog: Catalog | None = None,
    filename: str = "<script>",
    extra: Iterable[Diagnostic] = (),
) -> list[Diagnostic]:
    """All findings for one script. ``extra`` takes findings from other layers. Raises SyntaxError."""
    found = static_diagnostics(source, filename)
    if catalog is not None:
        found += catalog_diagnostics(source, catalog, filename)
    found += extra
    return finalize(source, found)


def catalog_diagnostics(source: str, catalog: Catalog, filename: str = "<script>") -> list[Diagnostic]:
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
    return found
