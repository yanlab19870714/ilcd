#!/bin/bash
#SBATCH -p general
#SBATCH -c 2
#SBATCH --mem=16G
#SBATCH -t 1:00:00
#SBATCH -J ilcd
# Run one ilcd command as a batch job:  sbatch -A $ILCD_SLURM_ACCOUNT --export=ALL jobs/ilcd.sh <ilcd arguments>
# For job arrays, "{task}" and "{ntasks}" in the arguments become SLURM_ARRAY_TASK_ID and SLURM_ARRAY_TASK_COUNT,
# e.g. sbatch --array=1-40 jobs/ilcd.sh data_src fetch-legacy --from-file files.txt --chunk {task}/{ntasks}
set -eo pipefail
module load "${ILCD_CONDA_MODULE:-conda/26.3.2}"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "$ILCD_ENV_ROOT/ilcd"
args=()
for a in "$@"; do
    a=${a//\{task\}/${SLURM_ARRAY_TASK_ID:-1}}
    args+=("${a//\{ntasks\}/${SLURM_ARRAY_TASK_COUNT:-1}}")
done
echo "$(date -Is) $(hostname) ilcd ${args[*]}"
ilcd "${args[@]}"
echo "$(date -Is) done"
