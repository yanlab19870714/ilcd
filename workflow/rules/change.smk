# Change detection for one site (docs/design.md section 7.1): DoD and M3C2 per cell of the site, then objects.
#
# The site names the scope whose model corrects it; only region scopes run here (the statewide scope has no
# workflow yet). The region's upper half (model, cv + status) is a prerequisite and is built by rules/upper.smk.
# Per-cell steps run in config["site_chunks"] (default 10) chunks with --skip-existing, as in rules/upper.smk.

from ilcd.common import config as C, ids as I

NS = int(config.get("site_chunks", 10))
SITE_CHUNKS = range(1, NS + 1)


def _s(rel):
    return f"{ROOT}/sites/{{site}}/{rel}"


def site_region(wc):
    region = I.parse_scope(C.site_scope(wc.site))
    if region is None:
        raise WorkflowError(f"site {wc.site}: statewide scope has no workflow yet; name a region scope")
    return region


def site_model(wc):
    r = site_region(wc)
    return [_m(f"regions/{r}/align/model", "align.fit"), _m(f"regions/{r}/eval", "eval.cv")]


rule site_cells:
    output: _s("lists/cells.txt")
    resources: runtime=10, mem_mb=4000
    shell: "ilcd change site-cells {wildcards.site} > {output}"


rule change_dod:
    input: cells=_s("lists/cells.txt"), model=site_model
    output: touch(_s("chunks/dod.{k}.done"))
    params: region=site_region
    resources: runtime=240, mem_mb=48000, cpus_per_task=2
    shell: "ilcd change dod {params.region} --from-file {input.cells} --chunk {wildcards.k}/{NS} --skip-existing"


rule change_m3c2:
    input: cells=_s("lists/cells.txt"), model=site_model
    output: touch(_s("chunks/m3c2.{k}.done"))
    resources: runtime=480, mem_mb=64000, cpus_per_task=8
    shell: "ilcd change m3c2 {wildcards.site} --from-file {input.cells} --chunk {wildcards.k}/{NS} --skip-existing"


rule change_objects:
    input: expand(_s("chunks/m3c2.{k}.done"), k=SITE_CHUNKS, allow_missing=True)
    output: _m("sites/{site}/change", "change.objects")
    resources: runtime=240, mem_mb=64000
    shell: "ilcd change objects {wildcards.site}"


rule change:
    """DoD and M3C2 of every site cell, and the site's change objects."""
    input:
        expand(_s("chunks/dod.{k}.done"), k=SITE_CHUNKS, allow_missing=True),
        _m("sites/{site}/change", "change.objects"),
    output: touch(f"{ROOT}/sites/{{site}}/.change.done")
