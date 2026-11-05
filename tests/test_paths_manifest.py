import json

import pytest

from ilcd.common import io, manifest, paths


def test_paths_need_data_root(monkeypatch):
    monkeypatch.delenv("ILCD_DATA_ROOT", raising=False)
    with pytest.raises(paths.ConfigurationError):
        paths.data_root()


def test_paths_layout(data_root):
    url = "https://opentopography.s3.sdsc.edu/pc-bulk/IN_2011_2013/West_IN/Monroe/ot_in2011_31501470_12_1.laz"
    assert paths.cache_legacy(url) == data_root / "cache/legacy/IN_2011_2013/West_IN/Monroe/ot_in2011_31501470_12_1.laz"
    assert paths.file_dir("W.ot_in2011_31501470_12_1") == data_root / "files/W.ot_in2011_31501470_12_1"
    assert paths.cell_dir("E0548N4330") == data_root / "cells/E0548N4330"
    assert paths.scope_dir("region:monroe") == data_root / "regions/monroe"
    assert paths.scope_dir("statewide") == data_root / "statewide/scope"
    assert paths.model_dir("statewide", "icp") == data_root / "statewide/scope/baselines/icp/model"
    assert paths.catalog("legacy_files") == data_root / "statewide/catalog/legacy_files.parquet"
    assert paths.relative(data_root / "cells/E0548N4330/x") == "cells/E0548N4330/x"
    for bad in (lambda: paths.file_dir("ot_in2011"), lambda: paths.cell_dir("E548N4330"), lambda: paths.catalog("x")):
        with pytest.raises(ValueError):
            bad()


def test_contract_paths_match_paths_module():
    """The `path` of the per-unit contracts is what paths.py produces (catches drift between spec and code)."""
    from ilcd.common import contracts

    assert contracts.get("passes").path[0] == "files/{file_id}/passes.parquet"
    assert contracts.get("obs").path[0] == "cells/{cell_id}/refs/obs.parquet"
    assert contracts.get("dod").path[0] == "cells/{cell_id}/change/dod.tif"
    assert contracts.get("legacy_files").path[0] == "statewide/catalog/legacy_files.parquet"
    assert contracts.get("datum").path[0] == "statewide/deliveries/{delivery_id}/datum.json"


def test_step_writes_manifest_only_on_success(data_root):
    out = data_root / "files" / "W.x"
    with manifest.Step("flightlines.passes", out_dir=out, params={"gap_s": 5.0}, unit="W.x") as step:
        p = io.write_empty_table(out / "passes.parquet", "passes")
        step.output(p, "passes")
        step.empty("no pass with enough points")
    doc = manifest.read(step.path)
    assert doc["status"] == "empty" and doc["outputs"][0]["path"] == "files/W.x/passes.parquet"
    assert doc["params_sha256"] == manifest.params_sha256({"gap_s": 5.0})
    failed = data_root / "files" / "W.y"
    with pytest.raises(RuntimeError):
        with manifest.Step("flightlines.passes", out_dir=failed, params={}) as step:
            raise RuntimeError("boom")
    assert not (failed / "flightlines.passes.manifest.json").exists()
    assert json.loads(step.path.read_text() if step.path.exists() else "{}") == {}
