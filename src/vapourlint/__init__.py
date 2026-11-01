"""VapourLint: a static analyzer for VapourSynth scripts."""

from .catalog import Catalog, CatalogUnavailable, discover
from .checks import Diagnostic, check
from .manifest import Call, Import, Manifest, analyze

__version__ = "0.0.2.dev0"

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
