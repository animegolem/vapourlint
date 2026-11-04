# VapourLint

A static analyzer for [VapourSynth](https://www.vapoursynth.com) scripts.

Today a script's mistakes surface when it runs: a clip in the wrong
format, a missing color matrix, a frame range that is off by one. Generic
Python linters see neither plugins nor clip formats. VapourLint checks a
script using what VapourSynth itself knows.

**Status: early.** The released 0.0.1 has the `manifest` command. The
development version adds `check`, `catalog` and `graph`. The checks listed
under *Planned* do not exist yet.

## Install

```sh
pip install vapourlint
```

VapourLint has no dependencies and never imports VapourSynth itself.
`manifest` and the static rules of `check` work without VapourSynth. The
catalog rules of `check`, and `catalog` itself, read plugin signatures from
a VapourSynth installation (see *Catalog*).

## Commands

```sh
vapourlint manifest script.vpy   # imports and plugin calls, nothing run
vapourlint check script.vpy      # static rules, plus catalog rules when VapourSynth is found
vapourlint catalog               # the installed plugins VapourLint sees
vapourlint graph script.vpy      # each line's clips (runs the script's Python)
```

Each takes `--json` for machine-readable output.

### manifest

Reads a script without running it and lists every import, every plugin
call written against the core (`core.std.Trim(...)`,
`vs.core.bs.VideoSource(...)`) with line, column and string arguments, and
the plugin namespaces used.

```text
episode.vpy
  plugins: bs, resize, std
  imports: vapoursynth (1), vsdenoise (2)
  calls:
    4:7      bs.VideoSource  "ep01.mkv"
    5:7      std.Crop
    6:7      resize.Spline36
```

The core is recognised as `vapoursynth.core`, `vs.core`, `from vapoursynth
import core` (with any alias), or a name assigned from one of those or
from the legacy `vs.get_core()`.

### check

Reports problems without running the script. Each finding has a severity:
**error**, **warning** or **info**.

```text
episode.vpy:4:7: error: unknown-argument: std.Trim has no argument 'frist'
episode.vpy:9:7: warning: range-literal: _Range=1 means full: _Range is numbered the other way from _ColorRange; write vs.RANGE_FULL
```

**Static rules** read the script's text only; they need nothing installed:

| Rule | Severity | Finds |
|---|---|---|
| `callback-signature` | error | a `std.ModifyFrame` selector that cannot take the keywords `n` and `f`, or a `std.FrameEval` eval that cannot take `n` (and `f` with `prop_src`). These fail only when a frame is rendered. Lambdas, functions defined in the script and `functools.partial` of them are checked; bound methods and imported functions are not |
| `late-global` | warning | a frame callback that reads a module-level name the script assigns again later: frames render after the script finishes, so every frame sees the later value |
| `range-literal` | warning | `_Range` set to a bare 0 or 1. `_Range` is numbered the other way from the deprecated `_ColorRange` (0 = limited); write `vs.RANGE_LIMITED` or `vs.RANGE_FULL` |
| `replace-ranges-convention` | info | vstools' `replace_ranges` or `rfs` without `exclusive=`: its default is inclusive and can be switched process-wide. Other libraries' `rfs` (fvsfunc, awsmfunc, older lvsfunc) has no such parameter and is not flagged |
| `deprecated-prop` | info | `_ColorRange`, which VapourSynth translates to `_Range` |

**Catalog rules** check every plugin call against the installed plugins'
signatures:

| Rule | Severity | Finds |
|---|---|---|
| `unknown-function` | error | `core.std.Trimm(...)`: the namespace exists but has no such function |
| `unknown-argument` | error | `clip.std.Trim(frist=0)`: a keyword the function does not declare |
| `missing-argument` | error | `core.std.Trim(first=0)`: a required parameter nobody supplies |

Method-form calls (`clip.std.Trim(...)`) are checked too: the clip fills
the function's first parameter, as VapourSynth does. A call using `*args`
or `**kwargs` is not checked for missing arguments, and a function that
accepts arbitrary keywords (such as `std.SetFrameProps`) is not checked for
unknown ones. When no VapourSynth installation is found, `check` says so on
stderr and runs the static rules alone.

Silence a finding on its line:

```python
clip = core.std.Trim(first=0)  # vapourlint: ignore[missing-argument]
clip = core.std.Trimm(clip)    # vapourlint: ignore
```

### graph, and check --graph

`manifest` and `check` never run the script. `graph` and `check --graph` do:
they execute the script's Python in a separate VapourSynth interpreter, one
top-level statement at a time, to build its graph and see what each line
produces. Run them only on scripts you would run yourself.

```text
episode.vpy  (VapourSynth R81)
  line  name             clip
     4  src              1280x720 YUV420P8 24000/1001, 480 frames
     5  clip             640x360 YUV420P8 24000/1001, 480 frames
     7  rgb              640x360 RGB24 24000/1001, 480 frames
     4  source bs.VideoSource: stand-in, file not found; declared format, fps, frames, height, width
```

Then frame 0 of each clip is rendered in script order, so a render-time
failure lands on the line that introduced it (`--no-render` skips this).

**Sources are stand-ins by default.** Opening real media can take long, and
some source filters write index files next to it. Each source call is
replaced by a blank clip; `--media` opens real files instead. Declare a
source's format in a comment on its line, or on a line of its own as the
default for every source:

```python
src = core.bs.VideoSource("ep01.mkv")  # vapourlint: source 1920x1080 yuv420p10 24000/1001 frames=34046 matrix=709 range=limited
```

What nobody declares is assumed, and VapourLint never reports an error that
may come from an assumed value. Such findings are shown as info, naming the
declaration that would check them.

**Graph rules:**

| Rule | Severity | Finds |
|---|---|---|
| `graph-error` | error | a line VapourSynth refuses to build (mismatched formats or sizes, a Trim past the end, ...) |
| `matrix-unspecified` | error | a YUV-to-RGB conversion with no matrix known: it builds, then fails when a frame is rendered |
| `render-error` | error | frame 0 of a line's clip fails to render for another reason |
| `slice-past-end` | warning | `clip[a:b]` with a literal end past the clip's length, which VapourSynth clamps silently |
| `source-stand-in` | info | a source was replaced by a stand-in, and what that leaves unchecked |

Limits: a compound statement (`if`, `for`, `with`) records its clips at its
first line; clips kept only inside lists or dicts are not recorded. The pass
stops after `--timeout` seconds (default 60) and stops the whole process.

### catalog

`check` and `catalog` read plugin signatures from a running VapourSynth
core, probed in a separate interpreter so that a crashing plugin cannot
take VapourLint down. Interpreters are tried in this order:

1. `--python PATH`;
2. the `VAPOURLINT_PYTHON` environment variable;
3. the interpreter running VapourLint, if it can import vapoursynth;
4. `python3` on `PATH`.

When none works, the error lists each interpreter tried and why. For CI
without VapourSynth, save a catalog once and reuse it:

```sh
vapourlint catalog --json > catalog.json
vapourlint check script.vpy --catalog catalog.json
```

## Exit status

| Status | Meaning |
|---|---|
| 0 | No errors or warnings (info findings never fail a run) |
| 1 | At least one error or warning (`check` only) |
| 2 | VapourLint could not do its job: unreadable file, invalid Python, or a catalog asked for with `--python` or `--catalog` that could not be read |

## Planned

- **Format requirements:** a clip of the wrong bit depth or family fed to a
  filter that builds anyway but misbehaves.
- **Configuration** in `[tool.vapourlint]`.
- **Diagnostics over LSP**, for any editor, merging BasedPyright's when it
  is installed.
- **Capability manifest:** files, network, models and plugins a script
  needs, for review before running it.

## Development

```sh
uv run pytest
```

Tests that need VapourSynth skip unless `VAPOURLINT_TEST_PYTHON` names a
Python that can import it.

Releases are published to PyPI by GitHub Actions when a `v*` tag matching
the package version is pushed, through PyPI Trusted Publishing; no API
token exists.

## License

MIT.
