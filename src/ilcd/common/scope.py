"""The units of a scope and concatenation of per-unit contract files over them.

A region scope's files and cells come from its selection (contract `selection`, written by data_src select). The
statewide scope is every file or cell that has the requested output.
"""

import pyarrow as pa

from . import contracts, ids, io, paths


def _selection(scope: str, unit: str) -> list[str]:
    p = paths.scope_dir(scope) / "data_src" / "selection.parquet"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `ilcd data_src select {ids.parse_scope(scope)}`")
    t = io.read_table(p, "selection").to_pandas()
    return sorted(t.loc[t["unit"] == unit, "unit_id"])


def files(scope: str) -> list[str]:
    if ids.parse_scope(scope) is None:
        return sorted(p.parent.name for p in (paths.data_root() / "files").glob("*/passes.parquet"))
    return _selection(scope, "file")


def cells(scope: str) -> list[str]:
    if ids.parse_scope(scope) is None:
        return sorted(p.parent.parent.name for p in (paths.data_root() / "cells").glob("*/refs/obs.parquet"))
    return _selection(scope, "cell")


def gather_cells(scope: str, contract: str, step=None) -> pa.Table:
    """Concatenate cells/<cell>/refs/<contract>.parquet over the scope (every cell must have it)."""
    parts, missing = [], []
    for c in cells(scope):
        p = paths.cell_dir(c) / "refs" / f"{contract}.parquet"
        if not p.exists():
            missing.append(c)
            continue
        if step is not None:
            step.input(p, contract)
        parts.append(io.read_table(p, contract))
    if missing:
        raise FileNotFoundError(f"{len(missing)} cells of {scope} lack {contract}, e.g. {missing[:3]}")
    return pa.concat_tables(parts) if parts else contracts.get(contract).empty_table()


def read(scope: str, module: str, contract: str) -> pa.Table:
    """A scope-level contract file <scope dir>/<module>/<contract>.parquet."""
    return io.read_table(paths.scope_dir(scope) / module / f"{contract}.parquet", contract)
