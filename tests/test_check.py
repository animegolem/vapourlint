import json
from pathlib import Path

import pytest

from vapourlint.catalog import Catalog
from vapourlint.checks import check
from vapourlint.cli import main
from vapourlint.manifest import analyze

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"
ENGINE_PYTHON = Path(__file__).parents[2] / "engine" / ".venv" / "bin" / "python"
HEADER = "from vapoursynth import core\nclip = core.std.BlankClip()\n"


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load(FIXTURE)


def codes(source, catalog):
    return [(d.line, d.code) for d in check(HEADER + source, catalog)]


# unknown-function


def test_unknown_function_in_an_installed_namespace(catalog):
    assert codes("core.std.Trimm(clip)\n", catalog) == [(3, "unknown-function")]


def test_unknown_namespace_is_not_this_rule(catalog):
    assert codes("core.notinstalled.Anything(clip)\n", catalog) == []


# unknown-argument


def test_unknown_keyword(catalog):
    assert codes("core.std.Trim(clip, frist=0)\n", catalog) == [(3, "unknown-argument")]


def test_known_keywords_pass(catalog):
    assert codes("core.std.Trim(clip, first=0, last=9)\n", catalog) == []


def test_functions_accepting_any_keyword_are_not_flagged(catalog):
    assert codes("core.std.SetFrameProps(clip, MyProp=1)\n", catalog) == []


def test_python_keyword_parameter_spelled_with_underscore(catalog):
    assert codes("core.kw.Weigh(clip, lambda_=0.5)\n", catalog) == []


# missing-argument


def test_missing_required_argument(catalog):
    assert codes("core.std.Trim(first=0)\n", catalog) == [(3, "missing-argument")]


def test_positional_arguments_fill_parameters_in_order(catalog):
    assert codes("core.std.Trim(clip, 0, 9)\n", catalog) == []


def test_method_form_supplies_the_first_parameter(catalog):
    assert codes("clip = clip.std.Trim(first=0)\n", catalog) == []
    assert codes("clip = clip.kw.Weigh()\n", catalog) == [(3, "missing-argument")]


def test_method_form_unknown_keyword(catalog):
    assert codes("clip = clip.std.Trim(frist=0)\n", catalog) == [(3, "unknown-argument")]


def test_starred_calls_are_not_checked_for_missing_arguments(catalog):
    assert codes("args = [clip]\nclip = core.std.Trim(*args)\n", catalog) == []


# method-form recognition


def test_method_form_needs_a_real_clip_function(catalog):
    # LoadPlugin's first parameter is a path, so x.std.LoadPlugin(...) is not a clip method.
    manifest = analyze(HEADER + "x.std.LoadPlugin('a')\nself.std.Trim(0)\n", catalog=catalog)
    assert [(c.name, c.form) for c in manifest.calls] == [("std.BlankClip", "core"), ("std.Trim", "method")]


def test_without_a_catalog_method_form_is_not_reported():
    assert [c.form for c in analyze(HEADER + "clip = clip.std.Trim(0)\n").calls] == ["core"]


# ignore comments


def test_ignore_one_code(catalog):
    assert codes("core.std.Trim(first=0)  # vapourlint: ignore[missing-argument]\n", catalog) == []


def test_ignore_a_different_code_keeps_the_finding(catalog):
    assert codes("core.std.Trim(first=0)  # vapourlint: ignore[unknown-argument]\n", catalog) == [
        (3, "missing-argument")
    ]


def test_ignore_everything_on_a_line(catalog):
    assert codes("core.std.Trimm(frist=0)  # vapourlint: ignore\n", catalog) == []


# command line


def test_cli_check_exit_codes_and_output(tmp_path, capsys):
    clean, dirty = tmp_path / "clean.vpy", tmp_path / "dirty.vpy"
    clean.write_text(HEADER + "core.std.Trim(clip, 0, 9)\n")
    dirty.write_text(HEADER + "core.std.Trim(clip, frist=0)\n")
    assert main(["check", str(clean), "--catalog", str(FIXTURE)]) == 0
    assert main(["check", str(dirty), "--catalog", str(FIXTURE)]) == 1
    assert f"{dirty}:3:1: error: unknown-argument: std.Trim has no argument 'frist'" in capsys.readouterr().out


def test_cli_check_json(tmp_path, capsys):
    script = tmp_path / "s.vpy"
    script.write_text(HEADER + "core.std.Trim(first=0)\n")
    assert main(["check", str(script), "--catalog", str(FIXTURE), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["vapoursynth"] == 81 and report["diagnostics"][0]["code"] == "missing-argument"


def test_cli_without_any_catalog_fails_clearly(tmp_path, capsys):
    script = tmp_path / "s.vpy"
    script.write_text(HEADER)
    assert main(["check", str(script), "--python", "/nonexistent/python3"]) == 2
    assert "no VapourSynth catalog" in capsys.readouterr().err


@pytest.mark.skipif(not ENGINE_PYTHON.exists(), reason="needs the engine environment (uv sync in engine/)")
def test_cli_against_the_real_core(tmp_path, capsys):
    script = tmp_path / "real.vpy"
    script.write_text(HEADER + "clip = clip.std.Trim(frist=0)\nclip = core.resize.Bicubic(clip, 640, 360)\n")
    assert main(["check", str(script), "--python", str(ENGINE_PYTHON)]) == 1
    out = capsys.readouterr().out
    assert "3:8: error: unknown-argument" in out and out.count("\n") == 1
