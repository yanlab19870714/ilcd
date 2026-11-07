# Upper half of the pipeline for one region: data_src -> flightlines -> refs -> align -> eval.
#
# Per-file and per-cell work runs in CHUNKS: the region's file and cell lists are split into config["chunks"]
# (default 40) parts, and each chunk is one Slurm job that processes its units in turn with --skip-existing. One job
# per unit was tried first: Slurm's queueing overhead made Monroe's 5,000 short steps take over a day (2026-11-05).
# Snakemake job groups were also tried: a group's resources are summed over its members, so groups asked for
# terabytes of memory and were rejected.
#
# Every unit still writes its own manifest; a chunk's marker only says the chunk finished. --skip-existing skips a unit
# only when its manifest has the current params and unchanged inputs (docs/design.md section 10), so forcing a chunked
# rule recomputes the units whose params or inputs changed. After a code change, run the ilcd command without the flag.
#
# Region runs read the statewide catalogs (statewide.smk) and never write them, so regions may run concurrently. The
# statewide stage must have finished first; it is an `ancient` input, so a region run never reschedules it.

from ilcd.common import paths as P

ROOT = str(P.data_root())
N = int(config.get("chunks", 40))
CHUNKS = range(1, N + 1)


def _m(rel, step):
    return f"{ROOT}/{rel}/{step}.manifest.json"


def _r(rel):
    return f"{ROOT}/regions/{{region}}/{rel}"


checkpoint select:
    input: ancient(STATEWIDE_DONE)
    output:
        _m("regions/{region}/data_src", "data_src.select"),
        files=_r("lists/files.txt"),
        cells=_r("lists/cells.txt"),
    resources: runtime=20, mem_mb=8000
    shell:
        "ilcd data_src select {wildcards.region} && "
        "ilcd data_src list {wildcards.region} file > {output.files} && "
        "ilcd data_src list {wildcards.region} cell > {output.cells}"


rule passes:
    input: _r("lists/files.txt"), ancient(STATEWIDE_DONE)
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
    input: _m("regions/{region}/refs", "refs.screen"), _m("regions/{region}/align/model", "align.fit")
    output: _m("regions/{region}/eval", "eval.cv")  # also status.parquet
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
