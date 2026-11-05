"""align: fit and apply the legacy correction model of a scope.

Owns:   model (model.json + members.parquet + stage2.npz)
Reads:  obs, overlap_obs, lines, line_members, blocks
Public: ilcd.align.apply (the only code that evaluates a model; also used by change, eval and baselines)

Stage 1 (one sparse least-squares problem): offset per line, one along-track trend per block, one scan-angle
B-spline per block (8 knots), horizontal forward/left shift per line from sloped ground (pooled per block below
align.stage1.hshift_min_points), vertical offset per 3DEP batch. Overlap observations tie lines of different blocks
and deliveries. Stage 2: per-line smooth B-spline field (1 km knots, smoothness penalty) fitted on road patches.

Planned files: design.py (design matrix), stage1.py, stage2.py, solve.py, apply.py, model.py (read/write).
"""
