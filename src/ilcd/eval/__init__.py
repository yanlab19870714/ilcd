"""eval: score corrections and change maps; never changes data.

Owns:   cv, status, injection, overlap_check, change_eval
Reads:  model (via align.apply), obs, overlap_obs, lines, objects, labels

Corrections: cross-validation by cell folds, overlap agreement (within/between blocks and deliveries), synthetic
change injection (share absorbed by each stage), per-cell per-line status. Change detection: scores against expert
labels; agreement with M3C2 is reported separately and never as accuracy.

Planned files: cv.py, overlap.py, injection.py, status.py, uncertainty.py, change_eval.py.
"""
