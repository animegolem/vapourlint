# VapourLint

A static analyzer for [VapourSynth](https://www.vapoursynth.com) scripts.

Today a script's mistakes surface when it runs: a clip in the wrong
format, a missing color matrix, a frame range that is off by one. Generic
Python linters see neither plugins nor clip formats. VapourLint checks a
script using what VapourSynth itself knows.

**Status: early.** The released 0.0.1 has the `manifest` command. The
development version adds `check` and `catalog`. The checks listed under
*Planned* do not exist yet.

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
| `callback-signature` | error | a `std.ModifyFrame` selector that cannot take the keywords `n` and `f`, or a `std.FrameEval` eval that cannot take `n` (and `f` with `prop_src`). These fail only when a frame is rendered |
| `late-global` | warning | a frame callback that reads a module-level name the script assigns again later: frames render after the script finishes, so every frame sees the later value |
| `range-literal` | warning | `_Range` set to a bare 0 or 1. `_Range` is numbered the other way from the deprecated `_ColorRange` (0 = limited); write `vs.RANGE_LIMITED` or `vs.RANGE_FULL` |
| `replace-ranges-convention` | warning | `replace_ranges` or `rfs` without `exclusive=`: its default is inclusive and can be switched process-wide |
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

- **Graph pass:** build the script's graph without rendering it and record
  each line's clip format, size, length and frame rate, with stand-ins for
  missing source media. This enables checks such as: YUV converted to RGB
  with no matrix set; a clip of the wrong bit depth fed to a filter; frame
  ranges outside their clip.
- **Configuration** in `[tool.vapourlint]`.
- **Diagnostics over LSP**, for any editor, merging BasedPyright's when it
  is installed.
- **Capability manifest:** files, network, models and plugins a script
  needs, for review before running it.

## License

MIT.
