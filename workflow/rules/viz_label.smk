# The review page of one site (docs/design.md section 7.2): scene sample, scene data in chunks, overview, page.
# Needs the site's change products (rules/change.smk). Scene data fetches orthophotos, so its jobs need network.

NV = int(config.get("scene_chunks", 10))
SCENE_CHUNKS = range(1, NV + 1)


rule viz_scenes:
    input: _m("sites/{site}/change", "change.objects")
    output: _m("sites/{site}/viz", "viz_label.scenes")
    resources: runtime=30, mem_mb=8000
    shell: "ilcd viz_label scenes {wildcards.site}"


rule viz_scene_ids:
    input: _m("sites/{site}/viz", "viz_label.scenes")
    output: _s("lists/scenes.txt")
    resources: runtime=10, mem_mb=4000
    shell: "ilcd viz_label scene-ids {wildcards.site} > {output}"


rule viz_scene_data:
    input: ids=_s("lists/scenes.txt"), model=site_model
    output: touch(_s("chunks/scene.{k}.done"))
    resources: runtime=240, mem_mb=64000, cpus_per_task=2
    shell: "ilcd viz_label scene-data {wildcards.site} --from-file {input.ids} --chunk {wildcards.k}/{NV} --skip-existing"


rule viz_overview:
    input: _m("sites/{site}/change", "change.objects")
    output: _m("sites/{site}/viz", "viz_label.overview")
    resources: runtime=120, mem_mb=32000
    shell: "ilcd viz_label overview {wildcards.site}"


rule viz_page:
    input:
        expand(_s("chunks/scene.{k}.done"), k=SCENE_CHUNKS, allow_missing=True),
        _m("sites/{site}/viz", "viz_label.overview"),
    output: _m("sites/{site}/viz", "viz_label.page")
    resources: runtime=30, mem_mb=16000
    shell: "ilcd viz_label page {wildcards.site}"


rule viz:
    """The site's review page: sites/{site}/viz/index.html with its overview and scene files."""
    input: _m("sites/{site}/viz", "viz_label.page")
    output: touch(f"{ROOT}/sites/{{site}}/.viz.done")
