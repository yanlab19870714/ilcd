"""flightlines: passes per legacy file, then lines and blocks per scope (docs/design.md section 3).

Owns:   passes, lines, line_members, blocks
Reads:  points (data_src.points), legacy_headers, deliveries

Rules carried over from the exploration (history.md): passes split at GPS gaps > flightlines.gap_s; heading and speed
from the least-squares x(t), y(t) of all points; centre = midpoint of the pass extent; link by heading, time,
along-track distance = speed x time and a loose cross-track tolerance. Near-nadir ground track and flying height are
diagnostics only. Acceptance: Carroll lines match point_source_id with purity 1.00 both ways.

Planned files: passes.py (per file), link.py (per scope), blocks.py (per scope), qa.py (purity, day span).
"""
