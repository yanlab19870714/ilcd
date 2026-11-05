from pathlib import Path

from ilcd import cli

ROOT = Path(__file__).resolve().parents[1]


def test_contracts_md_is_generated_and_current():
    md = ROOT / "docs" / "contracts.md"
    assert md.read_text() == cli.docs(), "docs/contracts.md is stale: run `ilcd contracts docs > docs/contracts.md`"


def test_cli_runs():
    assert cli.main(["contracts", "list"]) == 0
    assert cli.main(["config", "check"]) == 0
