# Statewide catalog stage: catalogs -> every legacy file with its header -> legacy_headers -> datum test of every
# delivery -> deliveries. It runs once, before any region (jobs/snakemake.sh statewide catalog), and is the only
# writer of statewide/catalog and statewide/deliveries; region runs only read them. A delivery's time type and datum
# are thus decided from all its files, whichever region is run first.

from ilcd.common import paths as P

ROOT = str(P.data_root())
SW_N = int(config.get("statewide_chunks", 100))
SW_CHUNKS = range(1, SW_N + 1)
STATEWIDE_DONE = f"{ROOT}/statewide/.catalog.done"


def _sm(rel, step):
    return f"{ROOT}/{rel}/{step}.manifest.json"


rule catalog:
    output: _sm("statewide/catalog", "data_src.catalog")
    resources: runtime=60, mem_mb=16000
    shell: "ilcd data_src catalog"


checkpoint statewide_lists:
    input: rules.catalog.output
    output:
        files=f"{ROOT}/statewide/lists/files.txt",
        deliveries=f"{ROOT}/statewide/lists/deliveries.txt",
    resources: runtime=10, mem_mb=8000
    shell:
        "ilcd data_src list statewide file > {output.files} && "
        "ilcd data_src list statewide delivery > {output.deliveries}"


rule fetch_legacy:
    input: rules.statewide_lists.output.files
    output: touch(f"{ROOT}/statewide/chunks/fetch_legacy.{{k}}.done")
    resources: runtime=480, mem_mb=8000
    shell: "ilcd data_src fetch-legacy --from-file {input} --chunk {wildcards.k}/{SW_N} --skip-existing"


rule headers:
    input: expand(f"{ROOT}/statewide/chunks/fetch_legacy.{{k}}.done", k=SW_CHUNKS)
    output: _sm("statewide/catalog", "data_src.headers")
    resources: runtime=60, mem_mb=16000
    shell: "ilcd data_src headers"


def all_deliveries(wc):
    with open(checkpoints.statewide_lists.get().output.deliveries) as f:
        return [s.strip() for s in f if s.strip()]


rule datum:
    # after every file is fetched: the test reads its sample files from the cache
    input: rules.headers.output
    output: _sm("statewide/deliveries/{delivery}", "data_src.datum")
    resources: runtime=180, mem_mb=32000, cpus_per_task=4
    shell: "ilcd data_src datum {wildcards.delivery}"


rule deliveries:
    input:
        rules.headers.output,
        lambda wc: [_sm(f"statewide/deliveries/{d}", "data_src.datum") for d in all_deliveries(wc)],
    output: _sm("statewide/catalog", "data_src.deliveries")
    resources: runtime=10, mem_mb=8000
    shell: "ilcd data_src deliveries"


rule statewide_catalog:
    """Everything statewide that region runs read."""
    input: rules.deliveries.output
    output: touch(STATEWIDE_DONE)
