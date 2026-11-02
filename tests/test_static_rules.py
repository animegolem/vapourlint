from vapourlint.checks import check
from vapourlint.cli import main
from vapourlint.static_rules import static_diagnostics

HEADER = "import vapoursynth as vs\nfrom vapoursynth import core\nclip = core.std.BlankClip()\n"


def found(source):
    return [(d.line, d.code, d.severity) for d in check(HEADER + source)]


# callback-signature


def test_modifyframe_selector_must_take_f():
    assert found("def sel(n, frame):\n    return frame\nout = core.std.ModifyFrame(clip, clip, sel)\n") == [
        (6, "callback-signature", "error")
    ]


def test_modifyframe_selector_with_n_and_f_passes():
    assert found("out = core.std.ModifyFrame(clip, clip, selector=lambda n, f: f)\n") == []


def test_modifyframe_method_form_and_kwargs_callback():
    assert found("out = clip.std.ModifyFrame(clip, lambda n, frame: frame)\n") == [(4, "callback-signature", "error")]
    assert found("out = clip.std.ModifyFrame(clip, lambda **kw: kw['f'])\n") == []


def test_callback_with_an_extra_required_parameter():
    assert found("out = core.std.ModifyFrame(clip, clip, lambda n, f, extra: f)\n") == [(4, "callback-signature", "error")]
    assert found("out = core.std.ModifyFrame(clip, clip, lambda n, f, extra=1: f)\n") == []


def test_frameeval_needs_f_only_with_prop_src():
    assert found("out = core.std.FrameEval(clip, lambda n: clip)\n") == []
    assert found("out = core.std.FrameEval(clip, lambda n: clip, prop_src=clip)\n") == [(4, "callback-signature", "error")]
    assert found("out = core.std.FrameEval(clip, lambda n, f: clip, prop_src=clip)\n") == []


# late-global


def test_callback_reading_a_global_assigned_later():
    source = "GAIN = 1\nout = core.std.FrameEval(clip, lambda n: clip.std.Levels(gamma=GAIN))\nGAIN = 2\n"
    assert found(source) == [(5, "late-global", "warning")]


def test_callback_reading_a_global_assigned_only_before():
    assert found("GAIN = 1\nout = core.std.FrameEval(clip, lambda n: clip.std.Levels(gamma=GAIN))\n") == []


def test_callback_locals_and_parameters_are_not_globals():
    source = (
        "def pick(n, f):\n    clip = f\n    return clip\n"
        "out = core.std.ModifyFrame(clip, clip, pick)\nclip = core.std.BlankClip()\n"
    )
    assert found(source) == []


# range-literal and deprecated-prop


def test_bare_range_literals_warn():
    assert found("clip = core.std.SetFrameProps(clip, _Range=1)\n") == [(4, "range-literal", "warning")]
    assert found("clip = clip.std.SetFrameProp(prop='_Range', intval=0)\n") == [(4, "range-literal", "warning")]


def test_range_constants_pass():
    assert found("clip = core.std.SetFrameProps(clip, _Range=vs.RANGE_LIMITED)\n") == []


def test_colorrange_is_info_only():
    assert found("clip = core.std.SetFrameProps(clip, _ColorRange=1)\n") == [(4, "deprecated-prop", "info")]
    assert found("value = clip.get_frame(0).props['_ColorRange']\n") == [(4, "deprecated-prop", "info")]


# replace-ranges-convention


def test_replace_ranges_without_exclusive():
    source = "from vstools import replace_ranges\nout = replace_ranges(clip, clip, [(0, 10)])\n"
    assert found(source) == [(5, "replace-ranges-convention", "info")]


def test_replace_ranges_with_exclusive_passes():
    assert found("import vstools\nout = vstools.replace_ranges(clip, clip, [(0, 10)], exclusive=True)\n") == []


# behaviour


def test_ignore_comment_applies_to_static_rules():
    assert found("clip = core.std.SetFrameProps(clip, _Range=1)  # vapourlint: ignore[range-literal]\n") == []


def test_static_rules_need_nothing_installed():
    assert static_diagnostics(HEADER) == []


def test_check_never_runs_the_script(tmp_path, capsys):
    marker = tmp_path / "ran"
    script = tmp_path / "s.vpy"
    script.write_text(f"open({str(marker)!r}, 'w').write('x')\nclip = core.std.SetFrameProps(clip, _Range=1)\n")
    assert main(["check", str(script), "--python", "/nonexistent/python3"]) == 2  # explicit catalog failure
    assert main(["check", str(script)]) == 1  # implicit: static rules still run
    assert not marker.exists()


def test_info_findings_do_not_fail_the_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "")  # no python3 on PATH: static rules only
    script = tmp_path / "s.vpy"
    script.write_text(HEADER + "clip = core.std.SetFrameProps(clip, _ColorRange=1)\n")
    assert main(["check", str(script)]) == 0
    assert "info: deprecated-prop" in capsys.readouterr().out


def test_replace_ranges_through_a_vstools_alias():
    assert found("import vstools as vst\nout = vst.rfs(clip, clip, [(0, 10)])\n") == [(5, "replace-ranges-convention", "info")]


def test_other_libraries_rfs_is_not_flagged():
    # fvsfunc, awsmfunc and older lvsfunc have an rfs with no exclusive parameter.
    for module in ("fvsfunc as fvf", "awsmfunc as awf", "lvsfunc as lvf"):
        alias = module.split()[-1]
        assert found(f"import {module}\nout = {alias}.rfs(clip, clip, '[0 10]')\n") == []
    assert found("from fvsfunc import rfs\nout = rfs(clip, clip, '[0 10]')\n") == []


def test_callbacks_through_functools_partial():
    good = "from functools import partial\ndef pick(n, f, clip):\n    return clip\nout = core.std.FrameEval(clip, partial(pick, clip=clip), prop_src=clip)\n"
    assert found(good) == []
    missing_f = "from functools import partial\ndef pick(n, clip):\n    return clip\nout = core.std.FrameEval(clip, partial(pick, clip=clip), prop_src=clip)\n"
    assert found(missing_f) == [(7, "callback-signature", "error")]
    unbound = "import functools\ndef pick(n, clip):\n    return clip\nout = core.std.FrameEval(clip, functools.partial(pick))\n"
    assert found(unbound) == [(7, "callback-signature", "error")]
