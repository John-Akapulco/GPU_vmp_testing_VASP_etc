#!/bin/bash
# Un calcul du scan NSIM par tâche de job array. Soumis par scripts/09_nsim_scan.py submit :
#   sbatch --array=0-N%1 --gres=gpu:1 --ntasks=1 --cpus-per-task=8 run_nsim.slurm lists/nsim_<...>.txt
# Chaque ligne de la liste est un dossier nsim_scan/<système>/<fonct>/nsimXXX/rK (ou nsim_scan/_test/...).
#SBATCH --job-name=vnsim
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --exclusive
#SBATCH --output=logs/%x-%A_%a.out

LIST="$1"
DIR=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$LIST")
[ -d "$DIR" ] || { echo "ERREUR : dossier '$DIR' introuvable (ligne $((SLURM_ARRAY_TASK_ID + 1)) de $LIST)"; exit 1; }
cd "$DIR"
if [ -f OUTCAR ] && grep -q "General timing" OUTCAR; then echo "Déjà terminé : $DIR"; exit 0; fi

source /opt/vasp/vasp_env.sh
# Un rang MPI, OpenMP sur les cœurs attribués par Slurm (cgroup), threads groupés
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-8}
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export OMPI_MCA_coll_hcoll_enable=0

# Autres jobs présents sur le nœud au démarrage (ils peuvent influencer les temps)
squeue -h -t R -w "$SLURMD_NODENAME" -o "%i|%u|%b" | grep -v -e "^${SLURM_JOB_ID}|" -e "^${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}|" > node_jobs.txt
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader > gpu_info.txt 2>&1
# Suivi GPU toutes les 500 ms : mémoire et utilisation (GPU attribuée seulement)
nvidia-smi -i "$CUDA_VISIBLE_DEVICES" --query-gpu=timestamp,index,memory.used,utilization.gpu \
           --format=csv,noheader,nounits -lms 500 > gpu_monitor.csv 2>/dev/null &
MON=$!

echo "Début $(date -Is) | $DIR | vasp_std, 1 GPU ($CUDA_VISIBLE_DEVICES), OMP $OMP_NUM_THREADS | job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
T0=$(date +%s.%N)
mpirun -np 1 --bind-to none vasp_std > vasp.stdout 2>&1
RC=$?
T1=$(date +%s.%N)
kill $MON 2>/dev/null

cat > timing.json <<JSON
{"slurm_job": "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}", "node": "$SLURMD_NODENAME",
 "gpus": "$CUDA_VISIBLE_DEVICES", "omp": $OMP_NUM_THREADS,
 "return_code": $RC, "wall_s": $(echo "$T1 - $T0" | bc), "start": "$(date -d @${T0%.*} -Is)",
 "other_jobs_on_node": $(wc -l < node_jobs.txt)}
JSON
echo "Fin $(date -Is) | code $RC | $(sed -n 's/.*"wall_s": \([0-9.]*\).*/\1/p' timing.json) s"
exit $RC
