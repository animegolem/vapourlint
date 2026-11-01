import shutil
import subprocess
import time
from pathlib import Path

import pytest

from vapourlint import graph
from vapourlint.checks import check
from vapourlint.cli import main
from vapourlint.graph_rules import graph_diagnostics

ENGINE_PYTHON = Path(__file__).parents[2] / "engine" / ".venv" / "bin" / "python"
needs_engine = pytest.mark.skipif(not ENGINE_PYTHON.exists(), reason="needs the engine environment (uv sync in engine/)")
HEADER = "import vapoursynth as vs\nfrom vapoursynth import core\n"


# The source comment and the source plan need nothing installed.


def test_parse_pragma():
    assert graph.parse_pragma(" 1920x1080 yuv420p10 24000/1001 frames=1200 matrix=709 range=limited") == {
        "width": 1920, "height": 1080, "format": "YUV420P10", "fps": [24000, 1001],
        "frames": 1200, "matrix": 1, "range": 0,
    }  # fmt: skip
    assert graph.parse_pragma("gray16 matrix=2020 fps=30/1") == {"format": "GRAY16", "matrix": 9, "fps": [30, 1]}
    assert graph.parse_pragma("matrix=nonsense") == {}
    assert graph.parse_pragma("matrix=1") == {"matrix": 1} and graph.parse_pragma("matrix=99") == {}


def test_source_plan_line_pragma_overrides_the_default():
    source = (
        HEADER
        + "# vapourlint: source 1280x720 yuv420p8\n"
        + "a = core.bs.VideoSource('a.mkv')\n"
        + "b = core.lsmas.LWLibavSource('b.mkv')  # vapourlint: source 640x360 frames=10\n"
        + "c = core.std.BlankClip()\n"
    )
    plan = sorted(graph.source_plan(source).values(), key=lambda p: p["line"])
    assert [(p["name"], p["declared"]) for p in plan] == [
        ("bs.VideoSource", {"width": 1280, "height": 720, "format": "YUV420P8"}),
        ("lsmas.LWLibavSource", {"width": 640, "height": 360, "format": "YUV420P8", "frames": 10}),
    ]


# The graph pass itself runs in the engine's VapourSynth.


def run_graph(tmp_path, body, **options):
    script = tmp_path / "s.vpy"
    script.write_text(HEADER + body)
    record = graph.run(script, str(ENGINE_PYTHON), **options)
    source = script.read_text()
    return record, check(source, None, str(script), graph_diagnostics(source, record, str(script)))


def codes(diagnostics):
    return [(d.line, d.code, d.severity) for d in diagnostics]


@needs_engine
def test_each_line_records_its_clips(tmp_path):
    record, _ = run_graph(
        tmp_path,
        "clip = core.std.BlankClip(width=64, height=36, length=10)\n"
        "clip = core.resize.Bicubic(clip, 32, 18)\n"
        "if __name__ == '__vapoursynth__':\n    clip.set_output()\n",
    )
    assert [(b["line"], b["name"], b["width"], b["frames"]) for b in record["bindings"]] == [
        (3, "clip", 64, 10),
        (4, "clip", 32, 10),
    ]
    assert [o["index"] for o in record["outputs"]] == [0]
    assert record["errors"] == [] and record["rendered"] == 2


@needs_engine
def test_build_errors_land_on_the_innermost_script_line(tmp_path):
    _, found = run_graph(
        tmp_path,
        "def mix(a):\n"
        "    return core.std.Expr([a, core.std.BlankClip(a, format=vs.YUV444P16)], 'x y +')\n"
        "clip = core.std.BlankClip(format=vs.YUV420P8)\n"
        "out = mix(clip)\n",
    )
    assert codes(found) == [(4, "graph-error", "error")]


@needs_engine
def test_missing_matrix_is_found_by_rendering(tmp_path):
    _, found = run_graph(tmp_path, "clip = core.std.BlankClip(format=vs.YUV420P8)\nrgb = core.resize.Bicubic(clip, format=vs.RGB24)\n")
    assert codes(found) == [(4, "matrix-unspecified", "error")]


@needs_engine
def test_a_declared_matrix_passes(tmp_path):
    _, found = run_graph(
        tmp_path,
        "src = core.bs.VideoSource('x.mkv')  # vapourlint: source 64x36 yuv420p8 24/1 frames=10 matrix=709\n"
        "rgb = core.resize.Bicubic(src, format=vs.RGB24)\n",
    )
    assert codes(found) == [(3, "source-stand-in", "info")]


@needs_engine
def test_an_assumed_matrix_never_produces_an_error(tmp_path):
    _, found = run_graph(
        tmp_path,
        "src = core.bs.VideoSource('x.mkv')  # vapourlint: source 64x36 yuv420p8 24/1 frames=10\n"
        "rgb = core.resize.Bicubic(src, format=vs.RGB24)\n",
    )
    assert codes(found) == [(3, "source-stand-in", "info"), (4, "matrix-unspecified", "info")]


@needs_engine
def test_slice_past_the_end(tmp_path):
    _, found = run_graph(tmp_path, "clip = core.std.BlankClip(length=10)\nshort = clip[0:100]\n")
    assert codes(found) == [(4, "slice-past-end", "warning")]


@needs_engine
def test_slice_rule_is_skipped_when_the_length_is_assumed(tmp_path):
    _, found = run_graph(tmp_path, "clip = core.bs.VideoSource('x.mkv')\nshort = clip[0:100000]\n")
    assert [c for c in codes(found) if c[1] == "slice-past-end"] == []
    assert any("frames=10000" in d.message for d in found if d.code == "source-stand-in")


@needs_engine
def test_callback_failures_are_found_at_render_too(tmp_path):
    _, found = run_graph(
        tmp_path, "clip = core.std.BlankClip()\nout = core.std.ModifyFrame(clip, clip, lambda n, frame: frame)\n"
    )
    assert sorted(codes(found)) == [(4, "callback-signature", "error"), (4, "render-error", "error")]


@needs_engine
def test_no_render_builds_only(tmp_path):
    record, found = run_graph(
        tmp_path, "clip = core.std.BlankClip(format=vs.YUV420P8)\nrgb = core.resize.Bicubic(clip, format=vs.RGB24)\n", render=False
    )
    assert record["rendered"] == 0 and codes(found) == []


@needs_engine
def test_the_graph_pass_does_run_the_script(tmp_path):
    marker = tmp_path / "ran"
    run_graph(tmp_path, f"open({str(marker)!r}, 'w').write('x')\n")
    assert marker.exists()


@needs_engine
def test_a_hung_script_is_stopped(tmp_path):
    script = tmp_path / "s.vpy"
    script.write_text("import time\ntime.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(graph.GraphUnavailable, match="did not finish"):
        graph.run(script, str(ENGINE_PYTHON), timeout=3)
    assert time.monotonic() - started < 15


@needs_engine
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg to make a sample file")
def test_media_opens_real_files_only_when_asked(tmp_path):
    clip = tmp_path / "real.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x54:rate=24:duration=0.5",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)],
        check=True,
    )  # fmt: skip
    body = f"src = core.bs.VideoSource({str(clip)!r})\n"
    standin, _ = run_graph(tmp_path, body)
    real, _ = run_graph(tmp_path, body, media=True)
    assert standin["sources"][0]["mode"] == "stand-in" and standin["bindings"][0]["width"] == 1920
    assert real["sources"][0]["mode"] == "media" and real["bindings"][0]["width"] == 96


@needs_engine
def test_cli_check_with_graph(tmp_path, capsys):
    script = tmp_path / "s.vpy"
    script.write_text(HEADER + "clip = core.std.BlankClip(length=10)\nshort = clip[0:100]\n")
    assert main(["check", str(script), "--graph", "--python", str(ENGINE_PYTHON)]) == 1
    assert "4:9: warning: slice-past-end" in capsys.readouterr().out
    assert main(["check", str(script), "--python", str(ENGINE_PYTHON)]) == 0  # without --graph, nothing runs
