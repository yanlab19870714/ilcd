# Runbook

How to set up and run ilcd on a Slurm cluster. The examples are written for IU Big Red 200; the same steps apply to
other clusters with their own paths.

## 1. One-time setup

```bash
git clone git@github.com:yanlab19870714/ilcd.git ~/code/ilcd
cd ~/code/ilcd

# Machine-specific settings: put these in ~/.bashrc, never in the repository
export ILCD_DATA_ROOT=/N/project/<group>/indiana_lidar/ilcd     # data store (caches, catalogs, outputs)
export ILCD_ENV_ROOT=/N/project/<group>/indiana_lidar/envs      # conda environments
export ILCD_SLURM_ACCOUNT=<account>

# Environments: built by a batch job, because solving them on a login node is slow
sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/make_env.sh main ~/code/ilcd     # -> $ILCD_ENV_ROOT/ilcd

# Snakemake profile: copy the template and fill in the account (config/profiles/<machine>/ is git-ignored)
cp -r config/profiles/example config/profiles/bigred
```

To share the data store with a group, run `umask 002` before writing to it, and make sure every member is in the
group that owns `$ILCD_DATA_ROOT`. Each person uses their own cluster and GitHub accounts; credentials are never
shared.

## 2. Every session

```bash
module load conda/26.3.2
conda activate $ILCD_ENV_ROOT/ilcd
cd ~/code/ilcd
ilcd info                 # version, data root, config directory
ilcd config check         # every region, site and delivery override is valid
pytest                    # unit tests; no data needed, under a minute
```

## 3. Running the pipeline

Each run is a long-lived controller job; Snakemake submits every step as its own Slurm job. Submit from the
repository root:

```bash
sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/snakemake.sh statewide catalog   # once, before any region
sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/snakemake.sh carroll             # a region, up to model and eval
sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/snakemake.sh eastfork_doug change  # change products of a site
sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/snakemake.sh eastfork_doug viz     # its review page (after change)
```

The statewide stage fetches every legacy file (42,450 files, 0.48 TB), concatenates their headers, runs the datum
test of every delivery and writes `deliveries`. It is the only writer of `statewide/catalog` and
`statewide/deliveries`; region and site runs refuse to start until it has finished, and then only read those files,
so several regions can run at once. `ilcd data_src deliveries` stops with an error while any delivery lacks the
headers of all its files or a datum decision (`--partial` writes the table anyway, for inspection).

Snakemake works out which outputs are missing or out of date and submits each step as a Slurm job. Each output
directory gets a `<step>.manifest.json` that records what was run. Check a file against its contract with:

```bash
ilcd contracts check passes $ILCD_DATA_ROOT/files/W.ot_in2011_31501470_12_1/passes.parquet
```

## 4. Conventions on Big Red 200

- Do not run long work over ssh. The connection drops and takes foreground jobs with it. Submit anything longer than
  a minute with `sbatch`, or let Snakemake submit it.
- `general` partition: small jobs (1–4 cores, 1 h or less) start soonest. `gpu-debug` is limited to 1 h and 3–4 jobs
  per user.
- py4dgeo is pip-installed into the main environment with its numpy left as is (`envs/main.yml`); do not let pip
  upgrade numpy.
- Do not use rockyweb.usgs.gov: it rate-limits and then blocks the cluster's IP. 3DEP is read from the public EPT
  service.

## 5. Adding a region or a site

- **Region**: create `config/regions/<name>.yaml`. The footprint is a county name or a polygon file. Parameter
  overrides go under `params:`. Then run `ilcd config check`.
- **Site**: create `config/sites/<name>.geojson`. It holds one polygon feature in EPSG:4326, with the properties
  `site`, `kind` and `source`.
- **Delivery facts**: add them to `config/deliveries.yaml` only with evidence, for example a GPS week taken from
  acquisition metadata.
