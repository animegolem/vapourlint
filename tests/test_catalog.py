from pathlib import Path

import pytest

from vapourlint.catalog import Catalog, CatalogUnavailable, parse_signature, probe

FIXTURE = Path(__file__).parent / "fixtures" / "catalog.json"
ENGINE_PYTHON = Path(__file__).parents[2] / "engine" / ".venv" / "bin" / "python"


def test_parse_signature_types_flags_and_arrays():
    params, accepts_any = parse_signature("clip:vnode:all;first:int:opt;clips:vnode[]:all;expr:data[];")
    assert [(p.name, p.type, p.array, p.optional) for p in params] == [
        ("clip", "vnode", False, False),
        ("first", "int", False, True),
        ("clips", "vnode", True, False),
        ("expr", "data", True, False),
    ]
    assert not accepts_any


def test_parse_signature_any():
    params, accepts_any = parse_signature("clip:vnode:all;any")
    assert [p.name for p in params] == ["clip"] and accepts_any


def test_python_keyword_parameters_take_an_underscore():
    catalog = Catalog.load(FIXTURE)
    weigh = catalog.get("kw", "Weigh")
    assert weigh.param("lambda_").name == "lambda"
    assert weigh.params[1].python_name == "lambda_"


def test_on_clips_follows_the_first_parameter():
    catalog = Catalog.load(FIXTURE)
    assert catalog.get("std", "Trim").on_clips
    assert catalog.get("std", "Splice").on_clips  # a clip list still binds the receiver
    assert not catalog.get("std", "LoadPlugin").on_clips


def test_catalog_metadata():
    catalog = Catalog.load(FIXTURE)
    assert catalog.vapoursynth == 81 and catalog.namespaces == {"std", "resize", "kw"}


def test_missing_interpreter_is_explained():
    with pytest.raises(CatalogUnavailable, match="not found"):
        probe("/nonexistent/python3")


def test_interpreter_without_vapoursynth_is_explained(tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\necho 'ModuleNotFoundError: No module named vapoursynth' >&2\nexit 1\n")
    fake.chmod(0o755)
    with pytest.raises(CatalogUnavailable, match="No module named vapoursynth"):
        probe(fake)


@pytest.mark.skipif(not ENGINE_PYTHON.exists(), reason="needs the engine environment (uv sync in engine/)")
def test_real_probe_matches_vapoursynth():
    catalog = Catalog.from_json(probe(ENGINE_PYTHON))
    assert catalog.vapoursynth and catalog.vapoursynth >= 81
    trim = catalog.get("std", "Trim")
    assert [p.name for p in trim.params][:2] == ["clip", "first"] and trim.on_clips
    assert catalog.get("std", "SetFrameProps").accepts_any
    assert not catalog.get("std", "LoadPlugin").on_clips
