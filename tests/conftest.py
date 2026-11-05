import os

import pytest


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """An empty data store for one test."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("ILCD_DATA_ROOT", str(root))
    return root


def pytest_collection_modifyitems(config, items):
    skip_data = pytest.mark.skip(reason="needs ILCD_DATA_ROOT with downloaded data (marker 'data')")
    skip_net = pytest.mark.skip(reason="set ILCD_TEST_NETWORK=1 to run tests that download")
    for item in items:
        if "data" in item.keywords and not os.environ.get("ILCD_TEST_DATA_ROOT"):
            item.add_marker(skip_data)
        if "network" in item.keywords and os.environ.get("ILCD_TEST_NETWORK") != "1":
            item.add_marker(skip_net)
