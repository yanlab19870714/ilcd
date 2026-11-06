import pytest

from ilcd.common import config, contracts, paths
from ilcd.common.config import ParamError


def test_defaults_load_and_have_every_module():
    d = config.defaults()
    for m in ("data_src", "flightlines", "refs", "align", "eval", "change", "viz_label"):
        assert m in d
    p = config.default_params()
    assert p["flightlines.gap_s"] == 5.0
    assert p["flightlines.link.cross_tol_m"] == 1500.0
    assert p["align.stage1.trend"] == "block"


def test_merge_is_strict():
    base = {"a": {"b": 1.0, "c": "x", "d": True, "e": [1]}}
    assert config.merge(base, {"a": {"b": 2}})["a"]["b"] == 2
    with pytest.raises(ParamError, match="unknown parameter a.z"):
        config.merge(base, {"a": {"z": 1}})
    with pytest.raises(ParamError, match="not of the default's type"):
        config.merge(base, {"a": {"c": 3}})
    with pytest.raises(ParamError):
        config.merge(base, {"a": {"d": 1}})  # bool is not int
    with pytest.raises(ParamError, match="is a group"):
        config.merge(base, {"a": 5})
    assert base["a"]["b"] == 1.0  # not mutated


def test_params_access():
    p = config.Params({"x": {"y": 1}})
    assert p["x.y"] == 1 and p.section("x") == {"y": 1}
    with pytest.raises(ParamError):
        p["x.z"]
    assert len(p.sha256()) == 64


def test_repository_configs_are_valid():
    regions = config.region_names()
    assert {"carroll", "monroe", "bartholomew", "jackson"} <= set(regions)
    for r in regions:
        reg = config.load_region(r)
        assert reg.scope == f"region:{r}"
    assert "eastfork_doug" in config.site_names()
    site = config.load_site("eastfork_doug")
    assert site["features"][0]["properties"]["regions"] == ["bartholomew", "jackson"]
    assert site["features"][0]["properties"]["scope"] == "region:bartholomew_jackson"
    assert config.load_region("bartholomew_jackson").footprint["county"] == ["Bartholomew", "Jackson"]
    overrides = config.load_delivery_overrides()
    assert overrides["W_Carroll"]["psid_meaning"] == "line"


def test_region_override_of_unknown_key_fails(tmp_path, monkeypatch):
    (tmp_path / "regions").mkdir()
    (tmp_path / "regions" / "x.yaml").write_text(
        "name: x\nfootprint: {county: X}\nparams: {flightlines: {gapp_s: 3}}\n"
    )
    monkeypatch.setenv("ILCD_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ParamError, match="flightlines.gapp_s"):
        config.load_region("x")
    (tmp_path / "regions" / "y.yaml").write_text("name: y\nfootprint: {county: Y, polygon: a.geojson}\n")
    with pytest.raises(contracts.ContractError):
        config.load_region("y")


def test_config_dir_default_is_repository():
    assert (paths.config_dir() / "regions" / "carroll.yaml").exists()
