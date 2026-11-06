#!/bin/bash
#SBATCH -p general
#SBATCH -c 1
#SBATCH --mem=8G
#SBATCH -t 2-00:00:00
#SBATCH -J ilcd-smk
# Run the workflow for one region as a long-lived controller job; Snakemake submits each step as its own Slurm job.
# Usage (from the repository root): sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/snakemake.sh <region> [target, default: upper] [snakemake args]
set -eo pipefail
umask 002
module load "${ILCD_CONDA_MODULE:-conda/26.3.2}"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$ILCD_ENV_ROOT/ilcd"
region=$1; target=${2:-upper}; shift; shift || true
cd "${SLURM_SUBMIT_DIR:?submit from the repository root}"  # sbatch runs a spooled copy of this script
snakemake -s workflow/Snakefile --profile config/profiles/${ILCD_PROFILE:-bigred} --config region="$region" \
    "$@" -- "$ILCD_DATA_ROOT/regions/$region/.$target.done"
