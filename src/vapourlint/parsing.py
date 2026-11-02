"""One way to parse a script."""

from __future__ import annotations

import ast
import warnings


def parse(source: str, filename: str = "<script>") -> ast.Module:
    """Parse without printing Python's own SyntaxWarnings: they describe the script, not VapourLint's findings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source, filename=filename)
