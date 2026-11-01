"""The plugin catalog: every installed plugin function and its parameters.

The catalog comes from a running VapourSynth core, probed in a separate
interpreter so that VapourLint itself never imports VapourSynth and a
crashing plugin cannot take it down. Interpreters are tried in order:

1. an explicit ``--python`` path;
2. the ``VAPOURLINT_PYTHON`` environment variable (an application that
   manages its own VapourSynth environment sets this);
3. the interpreter running VapourLint, if it can import vapoursynth;
4. ``python3`` on PATH.

A catalog can also be loaded from JSON written earlier, which is how CI
machines without VapourSynth and the tests use it.
"""

from __future__ import annotations

import importlib.util
import json
import keyword
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROBE_TIMEOUT = 15.0
CLIP_TYPES = {"vnode", "anode"}

# Runs inside the probed interpreter. Native plugins may write to stdout, so
# the result goes to a file named on the command line.
PROBE = r"""
import json, sys
import vapoursynth as vs
core = vs.core
try:
    version = core.core_version.release_major
except AttributeError:
    version = core.version_number()
plugins = [
    {
        "namespace": p.namespace,
        "identifier": p.identifier,
        "name": p.name,
        "functions": [
            {"name": f.name, "signature": f.signature, "returns": f.return_signature}
            for f in p.functions()
        ],
    }
    for p in core.plugins()
]
with open(sys.argv[1], "w", encoding="utf-8") as out:
    json.dump({"vapoursynth": version, "python": sys.executable, "plugins": plugins}, out)
"""


class CatalogUnavailable(Exception):
    """No interpreter could produce a catalog; the message says what was tried."""


@dataclass(frozen=True)
class Param:
    name: str  # as VapourSynth declares it
    type: str  # base type without the array marker, e.g. "vnode", "int"
    array: bool
    optional: bool

    @property
    def python_name(self) -> str:
        """The keyword a Python caller writes: VapourSynth appends "_" to Python keywords."""
        return f"{self.name}_" if keyword.iskeyword(self.name) else self.name


@dataclass(frozen=True)
class Function:
    namespace: str
    name: str
    params: tuple[Param, ...]
    accepts_any: bool  # the signature ends in "any": arbitrary keywords allowed
    returns: str

    @property
    def on_clips(self) -> bool:
        """Whether ``clip.namespace.name(...)`` exists: the first parameter takes a clip."""
        return bool(self.params) and self.params[0].type in CLIP_TYPES

    def param(self, python_name: str) -> Param | None:
        return next((p for p in self.params if p.python_name == python_name or p.name == python_name), None)


@dataclass(frozen=True)
class Catalog:
    functions: dict[tuple[str, str], Function]
    vapoursynth: int | None = None
    python: str | None = None

    @property
    def namespaces(self) -> set[str]:
        return {namespace for namespace, _ in self.functions}

    def get(self, namespace: str, name: str) -> Function | None:
        return self.functions.get((namespace, name))

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Catalog:
        functions: dict[tuple[str, str], Function] = {}
        for plugin in data.get("plugins", []):
            namespace = plugin["namespace"]
            for entry in plugin.get("functions", []):
                params, accepts_any = parse_signature(entry["signature"])
                functions[(namespace, entry["name"])] = Function(
                    namespace, entry["name"], params, accepts_any, entry.get("returns", "")
                )
        return cls(functions, data.get("vapoursynth"), data.get("python"))

    @classmethod
    def load(cls, path: Path) -> Catalog:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))


def parse_signature(signature: str) -> tuple[tuple[Param, ...], bool]:
    """Parse a VapourSynth signature such as ``clip:vnode;first:int:opt;``."""
    params: list[Param] = []
    accepts_any = False
    for segment in filter(None, signature.split(";")):
        if segment == "any":
            accepts_any = True
            continue
        name, kind, *flags = segment.split(":")
        array = kind.endswith("[]")
        params.append(Param(name, kind.removesuffix("[]"), array, "opt" in flags))
    return tuple(params), accepts_any


def probe(python: str | os.PathLike[str], timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    """Run the probe in ``python`` and return its JSON. Raises CatalogUnavailable."""
    with tempfile.TemporaryDirectory(prefix="vapourlint-") as directory:
        result = Path(directory) / "catalog.json"
        try:
            completed = subprocess.run(
                [os.fspath(python), "-c", PROBE, str(result)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            raise CatalogUnavailable(f"{python}: not found") from None
        except subprocess.TimeoutExpired:
            raise CatalogUnavailable(f"{python}: the probe did not finish within {timeout:g} s") from None
        if completed.returncode != 0 or not result.exists():
            last = completed.stderr.decode(errors="replace").strip().splitlines()
            raise CatalogUnavailable(f"{python}: {last[-1] if last else f'exit status {completed.returncode}'}")
        return json.loads(result.read_text(encoding="utf-8"))


def candidates(explicit: str | None = None) -> list[str]:
    """Interpreters to try, in order, without duplicates."""
    found: list[str] = []
    if explicit:
        found.append(explicit)
    if env := os.environ.get("VAPOURLINT_PYTHON"):
        found.append(env)
    if importlib.util.find_spec("vapoursynth") is not None:
        found.append(sys.executable)
    if on_path := shutil.which("python3"):
        found.append(on_path)
    return list(dict.fromkeys(found))


def discover_data(explicit: str | None = None) -> dict[str, Any]:
    """Probe each candidate until one answers; an explicit path is the only one tried."""
    tried: list[str] = []
    for python in [explicit] if explicit else candidates():
        try:
            return probe(python)
        except CatalogUnavailable as error:
            tried.append(str(error))
    if not tried:
        tried.append("no Python interpreter found (set VAPOURLINT_PYTHON or pass --python)")
    raise CatalogUnavailable("no VapourSynth catalog: " + "; ".join(tried))


def discover(explicit: str | None = None) -> Catalog:
    return Catalog.from_json(discover_data(explicit))
