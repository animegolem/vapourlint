"""Integration tests need a Python with VapourSynth; they skip without one.

Set VAPOURLINT_TEST_PYTHON to that interpreter, for example the one from
`uv venv && uv pip install vapoursynth vapoursynth-bestsource`.
"""

import os
from pathlib import Path

ENGINE_PYTHON = Path(os.environ.get("VAPOURLINT_TEST_PYTHON", "/nonexistent/vapoursynth-python"))
