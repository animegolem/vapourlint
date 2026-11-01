"""VapourLint: a static analyzer for VapourSynth scripts."""

from .manifest import Call, Import, Manifest, analyze

__version__ = "0.0.1"

__all__ = ["Call", "Import", "Manifest", "__version__", "analyze"]
