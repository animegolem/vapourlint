# VapourLint

A static analyzer for [VapourSynth](https://www.vapoursynth.com) scripts.

Today a script's mistakes surface when it runs: a clip in the wrong
format, a missing color matrix, a frame range that is off by one. Generic
Python linters see neither plugins nor clip formats. VapourLint checks a
script using what VapourSynth itself knows.

**Status: early.** Version 0.0.1 has one command, `manifest`. The checks
described under *Planned* do not exist yet.

## Install

```sh
pip install vapourlint
```

It has no dependencies and does not need VapourSynth installed for the
static manifest.

## Usage

```sh
vapourlint manifest script.vpy          # readable summary
vapourlint manifest script.vpy --json   # machine-readable
```

`manifest` reads a script without running it and lists:

- every import, with its line;
- every plugin call written against the core (`core.std.Trim(...)`,
  `vs.core.bs.VideoSource(...)`), with line, column and any string
  arguments;
- the plugin namespaces the script uses.

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

Exit codes: 0 on success, 2 when the file cannot be read or is not valid
Python (the message names the line).

## Limits of 0.0.1

- Method-form calls such as `clip.std.Trim(0, 9)` are not reported:
  telling them apart from ordinary attribute calls needs the catalog of
  installed plugins.
- A name that is first bound to the core and later rebound to something
  else is still treated as the core.

## Planned

- **Plugin catalog:** signatures from the installed core, so wrong
  argument names and types are caught, and method-form calls resolve.
- **Graph pass:** build the script's graph without rendering it and record
  each line's clip format, size, length and frame rate, with stand-ins for
  missing source media. This enables checks such as: YUV converted to RGB
  with no matrix set; a clip of the wrong bit depth fed to a filter; frame
  ranges outside their clip; `replace_ranges` called without an explicit
  range convention.
- **Diagnostics over LSP**, for any editor.
- **Capability manifest:** files, network, models and plugins a script
  needs, for review before running it.

## License

MIT.
