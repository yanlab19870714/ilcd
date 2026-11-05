# Acceptance record

The checks of docs/design.md section 11, with their results. Paths are under `$ILCD_DATA_ROOT` unless they start
with `runs/`, which is the exploration's directory `/N/project/yanlab_space/indiana_lidar/runs/`.

## Refactor step 2: Carroll, region `carroll_acceptance` (2026-11-05)

Commit: see `git log` of this file. Baseline: the exploration code (archive `indiana-lidar_2026-11-05`) re-run on
the 31 lines its current linking rule gives (`runs/2026-11-05_carroll_baseline31`).

| Check | Criterion | Result |
|---|---|---|
| Datum | W_Carroll decided `nad83` | `nad83`. Three or more informative samples per delivery; the 5 neighbouring deliveries are also `nad83` |
| Passes | Same passes as the exploration | 188 files compared: identical pass counts (491) and start times; headings differ by 0.05 deg (UTM vs State Plane grid convergence) |
| Lines | Purity 1.00 both ways against point_source_id | 31 lines, 1 block, purity 1.0000 / 1.0000 |
| Port of align + eval | The exploration's own observations reproduce its CV exactly | M3 3.92 cm, S_8 3.56 cm, narrow NMAD 5.45 / 5.03 cm, 12,290 segments: identical (`runs/2026-11-05_carroll_baseline31/paired`) |
| Observations | Stage-1 CV RMS of ilcd's observations no higher than the exploration's, under the same metric (1 km cell folds and segments) | ilcd 3.87 cm, exploration 4.11 cm; line offsets only: 4.33 cm against 4.53 cm |

Cross-validation of ilcd under other units, for reference. None of them is the exploration's metric:

| Folds / segments | line offsets | stage 1 | Note |
|---|---|---|---|
| cell / cell (default) | 4.33 cm | 3.87 cm | Leak-free |
| file / file | 3.70 cm | 3.26 cm | Leaks: a patch crossing a file boundary is seen by passes of both files |
| patch home file / patch home file | 4.42 cm | 4.07 cm | Leak-free, but 32,340 segments against the exploration's 12,290 |

Observation counts: 192,475 used (7,725 screened out); the exploration used 177,171.
