"""VapourLint: a static analyzer for VapourSynth scripts."""

from .catalog import Catalog, CatalogUnavailable, discover
from .checks import check
from .diagnostics import Diagnostic
from .manifest import Call, Import, Manifest, analyze

__version__ = "0.1.0"

__all__ = [
    "Call",
    "Catalog",
    "CatalogUnavailable",
    "Diagnostic",
    "Import",
    "Manifest",
    "__version__",
    "analyze",
    "check",
    "discover",
]
