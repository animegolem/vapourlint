import json

import pytest

from vapourlint import analyze
from vapourlint.cli import main


def names(source: str) -> list[str]:
    return [call.name for call in analyze(source).calls]


def test_vs_core_attribute():
    assert names("import vapoursynth as vs\nvs.core.std.BlankClip()\n") == ["std.BlankClip"]


def test_plain_module_name():
    assert names("import vapoursynth\nvapoursynth.core.std.BlankClip()\n") == ["std.BlankClip"]


@pytest.mark.parametrize(
    "header",
    [
        "from vapoursynth import core",
        "from vapoursynth import core as c\ncore = c",
        "import vapoursynth as vs\ncore = vs.core",
        "import vapoursynth as vs\ncore: vs.Core = vs.core",
        "import vapoursynth as vs\ncore = vs.get_core()",
    ],
)
def test_core_aliases(header):
    assert names(f"{header}\nclip = core.bs.VideoSource('a.mkv')\nclip = core.std.Trim(clip, 0, 9)\n") == [
        "bs.VideoSource",
        "std.Trim",
    ]


def test_lookalikes_are_not_reported():
    source = """
import os
import vapoursynth as vs
os.path.join("a", "b")
other.core.std.Trim()
vs.something.std.Trim()
"""
    assert names(source) == []


def test_method_form_is_not_reported_yet():
    source = "from vapoursynth import core\nclip = core.std.BlankClip()\nclip = clip.std.Trim(0, 1)\n"
    assert names(source) == ["std.BlankClip"]


def test_calls_inside_functions_and_arguments():
    source = """
from vapoursynth import core
def denoise(clip):
    return core.bm3d.Basic(core.resize.Point(clip, format=1))
"""
    assert names(source) == ["bm3d.Basic", "resize.Point"]


def test_string_arguments_are_recorded():
    call = analyze("from vapoursynth import core\ncore.bs.VideoSource(source='ep01.mkv', cachemode=0)\n").calls[0]
    assert call.strings == ["ep01.mkv"]


def test_columns_count_characters_not_bytes():
    source = 'from vapoursynth import core\nlabel = "日本語"; clip = core.std.BlankClip()\n'
    call = analyze(source).calls[0]
    assert (call.line, call.column) == (2, source.splitlines()[1].index("core") + 1)


def test_imports_and_plugins():
    manifest = analyze(
        "import vapoursynth as vs\nfrom vsdenoise import BM3D\nfrom . import helpers\n"
        "vs.core.std.Trim(vs.core.bs.VideoSource('a'), 0, 1)\n"
    )
    assert [(i.module, i.line) for i in manifest.imports] == [("vapoursynth", 1), ("vsdenoise", 2), (".", 3)]
    assert manifest.plugins == ["bs", "std"]


def test_cli_json(tmp_path, capsys):
    script = tmp_path / "s.vpy"
    script.write_text("from vapourlint_test import x\nfrom vapoursynth import core\ncore.std.BlankClip()\n")
    assert main(["manifest", str(script), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["plugins"] == ["std"] and data["calls"][0]["line"] == 3 and data["vapourlint"]


def test_cli_text(tmp_path, capsys):
    script = tmp_path / "s.vpy"
    script.write_text("from vapoursynth import core\ncore.bs.VideoSource('ep01.mkv')\n")
    assert main(["manifest", str(script)]) == 0
    out = capsys.readouterr().out
    assert "plugins: bs" in out and '2:1      bs.VideoSource  "ep01.mkv"' in out


def test_cli_syntax_error(tmp_path, capsys):
    script = tmp_path / "bad.vpy"
    script.write_text("from vapoursynth import core\nclip = core.std.BlankClip(\n")
    assert main(["manifest", str(script)]) == 2
    assert "bad.vpy:2:" in capsys.readouterr().err


def test_cli_missing_file(tmp_path, capsys):
    assert main(["manifest", str(tmp_path / "missing.vpy")]) == 2
    assert "missing.vpy" in capsys.readouterr().err
