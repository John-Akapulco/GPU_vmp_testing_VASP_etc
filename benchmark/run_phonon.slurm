#!/bin/bash
# Une chaîne de phonons (un matériau, une variante) par tâche de job array. Soumis par
# scripts/05_phonon_setup.py --submit :
#   sbatch --array=0-N%1 --gres=gpu:G --ntasks=G run_phonon.slurm lists/phonons_<variante>_<date>.txt
# Chaque ligne de la liste est un dossier phonons/<label>/<variante>.
#SBATCH --job-name=vphonon
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --output=logs/%x-%A_%a.out

LIST="$1"
DIR=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$LIST")
[ -d "$DIR" ] || { echo "ERREUR : dossier '$DIR' introuvable (ligne $((SLURM_ARRAY_TASK_ID + 1)) de $LIST)"; exit 1; }

source /opt/vasp/vasp_env.sh
source "$SLURM_SUBMIT_DIR/env.sh"
export OMP_NUM_THREADS=1
export OMPI_MCA_coll_hcoll_enable=0

squeue -h -t R -w "$SLURMD_NODENAME" -o "%i|%u|%b" | grep -v -e "^${SLURM_JOB_ID}|" -e "^${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}|" > "$DIR/node_jobs.txt"
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader > "$DIR/gpu_info.txt" 2>&1
echo "Début $(date -Is) | $DIR | $SLURM_NTASKS GPU ($CUDA_VISIBLE_DEVICES) | job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
python "$BENCH_DIR/scripts/phonon_pipeline.py" "$DIR"
RC=$?
echo "Fin $(date -Is) | code $RC"
exit $RC
