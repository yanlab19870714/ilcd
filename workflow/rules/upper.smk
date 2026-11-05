# Upper half of the pipeline for one region: data_src -> flightlines -> refs -> align -> eval.
# Every rule's output is the manifest its `ilcd` command writes on success (docs/design.md section 10), so a missing
# manifest means the step did not finish. Per-file and per-cell rules are grouped into batches of Slurm jobs with
# --group-components (see config/profiles/example/config.yaml).
#
# Statewide catalog files (legacy_headers, deliveries) are rewritten by region runs: run one region at a time.

from ilcd.common import paths as P

ROOT = str(P.data_root())


def _m(rel, step):
    return f"{ROOT}/{rel}/{step}.manifest.json"


def selection(wc, unit, core=False):
    """file_ids or cell_ids of the region, available once the select checkpoint has run."""
    checkpoints.select.get(region=wc.region)
    from ilcd.common import ids, scope

    sc = ids.scope(wc.region)
    return scope.files(sc) if unit == "file" else scope.cells(sc)


def region_files(wc):
    """The region's selected files plus every file its cells read (cells are shared; docs/design.md section 3)."""
    from ilcd.common import config
    from ilcd.data_src import regions

    return sorted(set(selection(wc, "file")) | set(regions.files_touching(selection(wc, "cell"), config.default_params())))


def region_deliveries(wc):
    from ilcd.data_src import catalog

    lf = catalog.read_legacy_files().set_index("file_id")
    return sorted(set(lf.loc[region_files(wc), "delivery_id"]))


rule catalog:
    output: _m("statewide/catalog", "data_src.catalog")
    resources: runtime=60, mem_mb=16000
    shell: "ilcd data_src catalog"


checkpoint select:
    input: rules.catalog.output
    output: _m("regions/{region}/data_src", "data_src.select")
    resources: runtime=20, mem_mb=8000
    shell: "ilcd data_src select {wildcards.region}"


rule fetch_legacy:
    input: rules.catalog.output
    output: _m("files/{file_id}", "data_src.fetch_legacy")
    group: "fetch_file"
    resources: runtime=10, mem_mb=8000
    shell: "ilcd data_src fetch-legacy {wildcards.file_id}"


rule headers:
    input: lambda wc: [_m(f"files/{f}", "data_src.fetch_legacy") for f in region_files(wc)]
    output: touch(f"{ROOT}/regions/{{region}}/data_src/.headers.done")  # the manifest is in statewide/catalog
    resources: runtime=20, mem_mb=8000
    shell: "ilcd data_src headers"


rule datum:
    input: rules.catalog.output
    output: _m("statewide/deliveries/{delivery}", "data_src.datum")
    resources: runtime=180, mem_mb=32000, cpus_per_task=4
    shell: "ilcd data_src datum {wildcards.delivery}"


rule deliveries:
    input:
        lambda wc: f"{ROOT}/regions/{wc.region}/data_src/.headers.done",
        lambda wc: [_m(f"statewide/deliveries/{d}", "data_src.datum") for d in region_deliveries(wc)],
    output: touch(f"{ROOT}/regions/{{region}}/data_src/.deliveries.done")
    resources: runtime=10, mem_mb=8000
    shell: "ilcd data_src deliveries"


# Per-file steps after the datum decisions run inside a region run (--config region=...): they wait for that
# region's deliveries table, which holds the datum and time domain of every delivery in the region.
DELIVERIES_DONE = f"{ROOT}/regions/{REGION}/data_src/.deliveries.done" if REGION else []


rule passes:
    input: _m("files/{file_id}", "data_src.fetch_legacy"), DELIVERIES_DONE
    output: _m("files/{file_id}", "flightlines.passes")
    group: "passes_file"
    resources: runtime=10, mem_mb=16000
    shell: "ilcd flightlines passes {wildcards.file_id}"


rule link:
    input:
        lambda wc: f"{ROOT}/regions/{wc.region}/data_src/.deliveries.done",
        lambda wc: [_m(f"files/{f}", "flightlines.passes") for f in selection(wc, "file")],
    output: _m("regions/{region}/flightlines", "flightlines.link")
    resources: runtime=30, mem_mb=16000
    shell: "ilcd flightlines link {wildcards.region}"


rule fetch_modern:
    input: rules.catalog.output
    output: _m("cells/{cell_id}", "data_src.fetch_modern")
    group: "fetch_cell"
    resources: runtime=15, mem_mb=8000
    shell: "ilcd data_src fetch-modern {wildcards.cell_id}"


rule fetch_ancillary:
    input: rules.catalog.output
    output: _m("cells/{cell_id}", "data_src.fetch_ancillary")
    group: "fetch_cell"
    resources: runtime=10, mem_mb=4000
    shell: "ilcd data_src fetch-ancillary {wildcards.cell_id}"


def cell_files(wc):
    from ilcd.common import config
    from ilcd.data_src import points

    return points.legacy_files_for(wc.cell_id, config.default_params())


rule refs_cell:
    input:
        _m("cells/{cell_id}", "data_src.fetch_modern"),
        _m("cells/{cell_id}", "data_src.fetch_ancillary"),
        lambda wc: [_m(f"files/{f}", "flightlines.passes") for f in cell_files(wc)],
    output: _m("cells/{cell_id}/refs", "refs.cell")
    group: "refs_cell"
    resources: runtime=20, mem_mb=24000
    shell: "ilcd refs cell {wildcards.cell_id}"


rule screen:
    input:
        lambda wc: _m(f"regions/{wc.region}/flightlines", "flightlines.link"),
        lambda wc: [_m(f"cells/{c}/refs", "refs.cell") for c in selection(wc, "cell")],
    output: _m("regions/{region}/refs", "refs.screen")
    resources: runtime=30, mem_mb=16000
    shell: "ilcd refs screen {wildcards.region}"


rule align_fit:
    input: _m("regions/{region}/refs", "refs.screen")
    output: _m("regions/{region}/align/model", "align.fit")
    resources: runtime=60, mem_mb=32000
    shell: "ilcd align fit {wildcards.region}"


rule eval_cv:
    input: _m("regions/{region}/refs", "refs.screen")
    output: _m("regions/{region}/eval", "eval.cv")
    resources: runtime=120, mem_mb=32000
    shell: "ilcd eval cv {wildcards.region}"


rule upper:
    """Everything up to the correction model and its cross-validation."""
    input:
        _m("regions/{region}/align/model", "align.fit"),
        _m("regions/{region}/eval", "eval.cv"),
    output: touch(f"{ROOT}/regions/{{region}}/.upper.done")
