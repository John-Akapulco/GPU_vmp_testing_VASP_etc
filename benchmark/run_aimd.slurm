#!/bin/bash
# Une dynamique moléculaire (segments successifs) par job. Soumis par scripts/08_aimd_setup.py --submit :
#   sbatch --gres=gpu:G --ntasks=G --cpus-per-task=T run_aimd.slurm runs_aimd/<nom> [segment max]
# T = OMP_NUM_THREADS par rang MPI (partie MLFF sur CPU).
#SBATCH --job-name=vaimd
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --output=logs/%x-%j.out

DIR="$1"
[ -f "$DIR/aimd.json" ] || { echo "ERREUR : '$DIR/aimd.json' introuvable"; exit 1; }

source /opt/vasp/vasp_env.sh
source "$SLURM_SUBMIT_DIR/env.sh"
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export OMP_STACKSIZE=512m
export OMPI_MCA_coll_hcoll_enable=0

squeue -h -t R -w "$SLURMD_NODENAME" -o "%i|%u|%b" | grep -v "^${SLURM_JOB_ID}|" > "$DIR/node_jobs.txt"
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader > "$DIR/gpu_info.txt" 2>&1
echo "Début $(date -Is) | $DIR | $SLURM_NTASKS GPU ($CUDA_VISIBLE_DEVICES), OMP $OMP_NUM_THREADS | job $SLURM_JOB_ID"
python "$BENCH_DIR/scripts/aimd_pipeline.py" "$DIR" "$2"
RC=$?
echo "Fin $(date -Is) | code $RC"
exit $RC
