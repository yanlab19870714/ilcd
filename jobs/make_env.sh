#!/bin/bash
#SBATCH -p general
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 2:00:00
#SBATCH -J ilcd-env
# Build (or update) a conda environment from envs/<name>.yml under $ILCD_ENV_ROOT and install ilcd into it.
# Usage: sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/make_env.sh <main|m3c2> <repo dir>
# Needs ILCD_ENV_ROOT; ILCD_CONDA_MODULE (default conda/26.3.2) names the cluster's conda module.
set -eo pipefail
umask 002   # the data store is shared by the group
echo "start $(date) on $(hostname)"
module load "${ILCD_CONDA_MODULE:-conda/26.3.2}"
source "$(conda info --base)/etc/profile.d/conda.sh"
name=$1; repo=$2
prefix=$ILCD_ENV_ROOT/$([ "$name" = main ] && echo ilcd || echo "ilcd-$name")
if [ -d "$prefix" ]; then mamba env update -y -p "$prefix" -f "$repo/envs/$name.yml" --prune
else mamba env create -y -p "$prefix" -f "$repo/envs/$name.yml"; fi
conda activate "$prefix"
pip install -e "$repo" --no-deps
python -c "import ilcd; print('ilcd', ilcd.__version__)"
echo "done $(date)"
