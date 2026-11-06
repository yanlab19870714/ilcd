# Upper half of the pipeline for one region: data_src -> flightlines -> refs -> align -> eval.
#
# Per-file and per-cell work runs in CHUNKS: the region's file and cell lists are split into config["chunks"]
# (default 40) parts, and each chunk is one Slurm job that processes its units in turn with --skip-existing. One job
# per unit was tried first: Slurm's queueing overhead made Monroe's 5,000 short steps take over a day (2026-11-05).
# Snakemake job groups were also tried: a group's resources are summed over its members, so groups asked for
# terabytes of memory and were rejected.
#
# Every unit still writes its own manifest; a chunk's marker only says the chunk finished. --skip-existing skips
# units that have a manifest, so to recompute after a code or parameter change, delete those manifests (or run the
# ilcd command without the flag).
#
# Statewide catalog files (legacy_headers, deliveries) are rewritten by region runs: run one region at a time.

from ilcd.common import paths as P

ROOT = str(P.data_root())
N = int(config.get("chunks", 40))
CHUNKS = range(1, N + 1)


def _m(rel, step):
    return f"{ROOT}/{rel}/{step}.manifest.json"


def _r(rel):
    return f"{ROOT}/regions/{{region}}/{rel}"


def region_deliveries(wc):
    """Deliveries of every file the region's cells read (cells are shared; docs/design.md section 3)."""
    checkpoints.select.get(region=wc.region)
    from ilcd.data_src import catalog

    lf = catalog.read_legacy_files().set_index("file_id")
    with open(f"{ROOT}/regions/{wc.region}/lists/files.txt") as f:
        files = [s.strip() for s in f if s.strip()]
    return sorted(set(lf.loc[files, "delivery_id"]))


rule catalog:
    output: _m("statewide/catalog", "data_src.catalog")
    resources: runtime=60, mem_mb=16000
    shell: "ilcd data_src catalog"


checkpoint select:
    input: rules.catalog.output
    output:
        _m("regions/{region}/data_src", "data_src.select"),
        files=_r("lists/files.txt"),
        cells=_r("lists/cells.txt"),
    resources: runtime=20, mem_mb=8000
    shell:
        "ilcd data_src select {wildcards.region} && "
        "ilcd data_src list {wildcards.region} file > {output.files} && "
        "ilcd data_src list {wildcards.region} cell > {output.cells}"


rule fetch_legacy:
    input: _r("lists/files.txt")
    output: touch(_r("chunks/fetch_legacy.{k}.done"))
    resources: runtime=180, mem_mb=8000
    shell: "ilcd data_src fetch-legacy --from-file {input} --chunk {wildcards.k}/{N} --skip-existing"


rule headers:
    input: expand(_r("chunks/fetch_legacy.{k}.done"), k=CHUNKS, allow_missing=True)
    output: touch(_r("data_src/.headers.done"))  # the manifest is in statewide/catalog
    resources: runtime=20, mem_mb=8000
    shell: "ilcd data_src headers"


rule datum:
    input: rules.catalog.output
    output: _m("statewide/deliveries/{delivery}", "data_src.datum")
    resources: runtime=180, mem_mb=32000, cpus_per_task=4
    shell: "ilcd data_src datum {wildcards.delivery}"


rule deliveries:
    input:
        _r("data_src/.headers.done"),
        lambda wc: [_m(f"statewide/deliveries/{d}", "data_src.datum") for d in region_deliveries(wc)],
    output: touch(_r("data_src/.deliveries.done"))
    resources: runtime=10, mem_mb=8000
    shell: "ilcd data_src deliveries"


rule passes:
    input: _r("lists/files.txt"), _r("data_src/.deliveries.done")
    output: touch(_r("chunks/passes.{k}.done"))
    resources: runtime=180, mem_mb=16000
    shell: "ilcd flightlines passes --from-file {input[0]} --chunk {wildcards.k}/{N} --skip-existing"


rule link:
    input: expand(_r("chunks/passes.{k}.done"), k=CHUNKS, allow_missing=True)
    output: _m("regions/{region}/flightlines", "flightlines.link")
    resources: runtime=30, mem_mb=16000
    shell: "ilcd flightlines link {wildcards.region}"


rule fetch_modern:
    input: _r("lists/cells.txt")
    output: touch(_r("chunks/fetch_modern.{k}.done"))
    resources: runtime=240, mem_mb=8000
    shell: "ilcd data_src fetch-modern --from-file {input} --chunk {wildcards.k}/{N} --skip-existing"


rule fetch_ancillary:
    input: _r("lists/cells.txt")
    output: touch(_r("chunks/fetch_ancillary.{k}.done"))
    resources: runtime=240, mem_mb=8000
    shell: "ilcd data_src fetch-ancillary --from-file {input} --chunk {wildcards.k}/{N} --skip-existing"


rule refs_cell:
    # a cell reads the passes of every file overlapping it: wait for the region's link (all passes exist)
    input:
        cells=_r("lists/cells.txt"),
        modern=_r("chunks/fetch_modern.{k}.done"),
        ancillary=_r("chunks/fetch_ancillary.{k}.done"),
        link=_m("regions/{region}/flightlines", "flightlines.link"),
    output: touch(_r("chunks/refs.{k}.done"))
    resources: runtime=360, mem_mb=32000, cpus_per_task=2
    shell: "ilcd refs cell --from-file {input.cells} --chunk {wildcards.k}/{N} --skip-existing"


rule screen:
    input: expand(_r("chunks/refs.{k}.done"), k=CHUNKS, allow_missing=True)
    output: _m("regions/{region}/refs", "refs.screen")
    resources: runtime=60, mem_mb=32000
    shell: "ilcd refs screen {wildcards.region}"


rule align_fit:
    input: _m("regions/{region}/refs", "refs.screen")
    output: _m("regions/{region}/align/model", "align.fit")
    resources: runtime=60, mem_mb=64000
    shell: "ilcd align fit {wildcards.region}"


rule eval_cv:
    input: _m("regions/{region}/refs", "refs.screen")
    output: _m("regions/{region}/eval", "eval.cv")
    resources: runtime=360, mem_mb=96000, cpus_per_task=4
    shell: "ilcd eval cv {wildcards.region}"


rule eval_injection:
    input: _m("regions/{region}/refs", "refs.screen")
    output: _m("regions/{region}/eval", "eval.injection")
    resources: runtime=360, mem_mb=96000, cpus_per_task=4
    shell: "ilcd eval injection {wildcards.region}"


rule upper:
    """Everything up to the correction model and its evaluation."""
    input:
        _m("regions/{region}/align/model", "align.fit"),
        _m("regions/{region}/eval", "eval.cv"),
        _m("regions/{region}/eval", "eval.injection"),
    output: touch(f"{ROOT}/regions/{{region}}/.upper.done")
