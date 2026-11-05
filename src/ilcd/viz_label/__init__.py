"""viz_label: pages for looking at change and labelling it.

Owns:   labels (GeoJSON, EPSG:4326)
Reads:  objects, points, imagery

3D before/after scenes per object (terrain meshes draped with same-year orthophotos), a BEV map page with polygon
drawing, and QGIS project export. All three write the same labels file.

Planned files: scenes.py, imagery.py, mesh.py, pages.py, templates/ (scene3d.html, bev.html), qgis/.
"""
