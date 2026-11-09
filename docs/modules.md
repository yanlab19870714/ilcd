# Modules

This page is a map of the code. The decisions behind it are in [design.md](design.md), and every file a module
writes is described in [contracts.md](contracts.md).

```
data_src ──► flightlines ──► refs ──► align ──► eval
    │                          │        │
    └──────────── points ──────┴──► change ──► viz_label ──► labels ──► eval
                                        ▲
                               baselines (same contracts)
```

| Module | One-line job | Unit of work | Writes | Status |
|---|---|---|---|---|
| `common` | IDs, grid, CRS, time, paths, contracts, I/O, config, manifests | — | manifests | **implemented** |
| `data_src` | Catalogs, downloads, datum per delivery, the raw point and ancillary readers | statewide, delivery, cell | `legacy_files`, `legacy_headers`, `deliveries`, `modern_batches`, `datum`, `selection` | **implemented** (OSM and CDL layers: later) |
| `flightlines` | Passes per file; lines and blocks per scope | file, scope | `passes`, `lines`, `line_members`, `blocks` | **implemented** |
| `refs` | Stable reference surfaces and their observations | cell, scope | `patches`, `obs`, `overlap_obs`, `screen` | **hard ground implemented**; roofs, natural and sloped ground: later |
| `align` | Fit and apply the correction model | scope | `model` | **stage 1 implemented**; stage 2, horizontal shift, overlap ties, apply: later |
| `eval` | Cross-validation, overlap check, injection, status, change scores | scope, site | `cv`, `status`, `injection`, `overlap_check`, `change_eval` | **cv implemented**; the rest: later |
| `change` | DoD, M3C2, change objects | cell, site | `dod`, `m3c2`, `objects` | planned (step 4) |
| `viz_label` | Site review page with 3-D scenes, labels | site | `scenes`, `labels`, pages | **site page and label import implemented**; blind labelling, QGIS: step 5 |
| `baselines` | Other methods, written to the same contracts | scope, site | as align/change | planned (step 5) |

## Rules every module follows

1. **Inputs and outputs are contract files.** Read and write them with `ilcd.common.io`, which validates them.
   - A module imports no other business module.
   - Two functions are the exception: `data_src.points` and `align.apply`.
2. **Every path comes from `ilcd.common.paths`.**
3. **Every parameter comes from `config`.** Look it up as `params["module.key"]`, never as a constant in the code.
   A new parameter is added to `src/ilcd/config/defaults.yaml` together with its unit.
4. **Every step runs inside `manifest.Step`.** No input means a valid empty output plus `step.empty(reason)`, not an
   exception.
5. **Every command is reachable as `ilcd <module> <command>`.** Snakemake rules only call these commands.
6. **Each module has tests.**
   - Unit tests need no data.
   - Data tests use the fixture area (marker `data`).

## Changing a contract

1. Edit `src/ilcd/schemas/<name>.v<N>.json`.
   - While the contract is a `draft`, change it in place.
   - Once it is `stable`, add `<name>.v<N+1>.json` instead and keep reading v<N>.
2. Run `ilcd contracts docs > docs/contracts.md`. A test fails if it is stale.
3. Update the owner module, then its readers. The table in design.md section 8 lists who reads what.

## Refactor steps

1. **Repository skeleton**: environments, `common`, all contracts, configs, docs. *(done)*
2. **data_src → flightlines → refs → align → eval on Carroll.** *(done 2026-11-06, see acceptance.md; flat roofs
   are still missing, see below)*
   - Lines must match point_source_id (purity 1.00 both ways).
   - The old observations run through ilcd reproduce the old CV exactly. Under the same metric, ilcd's own
     observations do no worse (design.md section 11).
3. **Monroe.** Week-seconds time, ITRF datum, forest cells, the clipped county-line files. *(done 2026-11-06)*
4. **Bartholomew + Jackson**: the first cross-county scope.
   - change and viz_label on site `eastfork_doug`.
   - Compare with the 325 objects from the exploration.
   - Flat roofs (refs.roof, already in defaults.yaml and align.stage1.fit_kinds): not detected yet. Rare outside
     towns (about one usable roof per tile) but common in Bloomington and Columbus, where they may be the best
     references. Implement after the site results, then decide by a CV ablation on Monroe and Bartholomew, as for
     CDL.
5. **baselines + blind expert labels**, scored by eval.
6. **Statewide**: catalog-wide datum and time checks, joint solve, DL surface segmentation.
