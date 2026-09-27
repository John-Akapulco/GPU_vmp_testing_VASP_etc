#!/bin/bash
# Un lot de N calculs VASP simultanés sur 1 GPU par tâche de job array. Soumis par
# scripts/07_concurrency.py --submit :
#   sbatch --array=0-N%1 --gres=gpu:1 --ntasks=8 run_conc.slurm lists/concurrency_<date>.txt
# Chaque ligne de la liste est un dossier runs_conc/<série>/<label>/n<N>_<mps|nomps>
# contenant copy-1 … copy-N.
#SBATCH --job-name=vconc
#SBATCH --gres=gpu:1
#SBATCH --ntasks=8
#SBATCH --output=logs/%x-%A_%a.out

LIST="$1"
DIR=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$LIST")
[ -d "$DIR" ] || { echo "ERREUR : dossier '$DIR' introuvable (ligne $((SLURM_ARRAY_TASK_ID + 1)) de $LIST)"; exit 1; }
cd "$DIR"
if [ -f timing.json ] && grep -q '"return_code": 0' timing.json; then echo "Déjà terminé : $DIR"; exit 0; fi
MODE=${DIR##*_}
COPIES=(copy-*)
N=${#COPIES[@]}

source /opt/vasp/vasp_env.sh
export OMP_NUM_THREADS=1
export OMPI_MCA_coll_hcoll_enable=0

squeue -h -t R -w "$SLURMD_NODENAME" -o "%i|%u|%b" | grep -v "^${SLURM_JOB_ID}|" > node_jobs.txt
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader > gpu_info.txt 2>&1

# Démon MPS propre au job : répertoires privés, n'interfère avec aucun autre utilisateur
if [ "$MODE" = mps ]; then
    export CUDA_MPS_PIPE_DIRECTORY=/tmp/mps_${SLURM_JOB_ID}/pipe
    export CUDA_MPS_LOG_DIRECTORY=/tmp/mps_${SLURM_JOB_ID}/log
    mkdir -p "$CUDA_MPS_PIPE_DIRECTORY" "$CUDA_MPS_LOG_DIRECTORY"
    nvidia-cuda-mps-control -d || { echo "ERREUR : démarrage MPS"; exit 1; }
fi

nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw \
           --format=csv,noheader,nounits -l 2 > gpu_monitor.csv 2>/dev/null &
MON=$!

echo "Début $(date -Is) | $DIR | $N copies, $MODE, GPU $CUDA_VISIBLE_DEVICES | job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
T0=$(date +%s.%N)
for c in "${COPIES[@]}"; do
    (
        cd "$c"
        t0=$(date +%s.%N)
        mpirun -np 1 --bind-to none vasp_std > vasp.stdout 2>&1
        rc=$?
        t1=$(date +%s.%N)
        echo "{\"return_code\": $rc, \"wall_s\": $(echo "$t1 - $t0" | bc), \"start\": \"$(date -d @${t0%.*} -Is)\"}" > timing.json
    ) &
done
wait
T1=$(date +%s.%N)
kill $MON 2>/dev/null
[ "$MODE" = mps ] && { echo quit | nvidia-cuda-mps-control; rm -rf /tmp/mps_${SLURM_JOB_ID}; }

RC=0
for c in "${COPIES[@]}"; do grep -q '"return_code": 0' "$c/timing.json" || RC=1; done
cat > timing.json <<JSON
{"slurm_job": "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}", "node": "$SLURMD_NODENAME",
 "gpus": "$CUDA_VISIBLE_DEVICES", "ncopies": $N, "mode": "$MODE",
 "return_code": $RC, "wall_s": $(echo "$T1 - $T0" | bc), "start": "$(date -d @${T0%.*} -Is)",
 "other_jobs_on_node": $(wc -l < node_jobs.txt)}
JSON
echo "Fin $(date -Is) | code $RC | $(sed -n 's/.*"wall_s": \([0-9.]*\).*/\1/p' timing.json) s"
exit $RC
