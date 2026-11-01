"""What every rule produces, and the ignore comments that silence it."""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]
IGNORE = re.compile(r"vapourlint:\s*ignore(?:\[([^\]]*)\])?")


@dataclass(frozen=True, order=True)
class Diagnostic:
    line: int
    column: int
    code: str
    message: str
    severity: Severity = "error"

    @property
    def counts(self) -> bool:
        """Errors and warnings fail a check; info never does."""
        return self.severity != "info"


def finalize(source: str, found: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Drop silenced and duplicate findings, in line order."""
    ignored = _ignored(source)
    return sorted({d for d in found if not _silenced(d, ignored)})


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
