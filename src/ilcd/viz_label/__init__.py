"""viz_label: the review page of a site, and its labels (docs/design.md section 7.2).

Owns:   scenes (GeoParquet), labels (GeoJSON, EPSG:4326)
Reads:  objects, object_members, m3c2, points + model, orthophotos (data_src.ancillary)

sample.py    review sample of objects, stratified, and their scene squares
scene.py     packed points, terrain grids and photos of one scene
overview.py  site map image from the M3C2 cells
page.py      index.html from templates/site.html with the objects embedded
labels.py    labels copied from the page -> contract labels

Blind labelling (polygons drawn without the objects) and QGIS export: refactor step 5.
"""
