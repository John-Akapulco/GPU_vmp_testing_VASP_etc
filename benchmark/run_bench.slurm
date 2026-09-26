#!/bin/bash
# Un calcul du benchmark par tâche de job array. Soumis par scripts/03_submit.py :
#   sbatch --array=0-N%1 --gres=gpu:G --ntasks=G run_bench.slurm lists/<liste>.txt
# Chaque ligne de la liste est un dossier runs/<fonct>_<calcul>/<label>/<variante>.
#SBATCH --job-name=vbench
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --output=logs/%x-%A_%a.out

LIST="$1"
DIR=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$LIST")
[ -d "$DIR" ] || { echo "ERREUR : dossier '$DIR' introuvable (ligne $((SLURM_ARRAY_TASK_ID + 1)) de $LIST)"; exit 1; }
cd "$DIR"
if [ -f OUTCAR ] && grep -q "General timing" OUTCAR; then echo "Déjà terminé : $DIR"; exit 0; fi

source /opt/vasp/vasp_env.sh
export OMP_NUM_THREADS=1
export OMPI_MCA_coll_hcoll_enable=0
EXE=$(sed -n 's/.*"exe": *"\([a-z_]*\)".*/\1/p' bench.json); EXE=${EXE:-vasp_std}

# Autres jobs présents sur le nœud au démarrage (ils peuvent influencer les temps)
squeue -h -t R -w "$SLURMD_NODENAME" -o "%i|%u|%b" | grep -v "^${SLURM_JOB_ID}|" > node_jobs.txt
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader > gpu_info.txt 2>&1
# Suivi GPU toutes les 2 s : utilisation, mémoire, puissance
nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw \
           --format=csv,noheader,nounits -l 2 > gpu_monitor.csv 2>/dev/null &
MON=$!

echo "Début $(date -Is) | $DIR | $EXE sur $SLURM_NTASKS GPU ($CUDA_VISIBLE_DEVICES) | job ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
T0=$(date +%s.%N)
mpirun -np "$SLURM_NTASKS" --bind-to none "$EXE" > vasp.stdout 2>&1
RC=$?
T1=$(date +%s.%N)
kill $MON 2>/dev/null

cat > timing.json <<JSON
{"slurm_job": "${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}", "node": "$SLURMD_NODENAME",
 "gpus": "$CUDA_VISIBLE_DEVICES", "ntasks": $SLURM_NTASKS, "cpus_on_node": ${SLURM_CPUS_ON_NODE:-0},
 "return_code": $RC, "wall_s": $(echo "$T1 - $T0" | bc), "start": "$(date -d @${T0%.*} -Is)",
 "other_jobs_on_node": $(wc -l < node_jobs.txt)}
JSON
echo "Fin $(date -Is) | code $RC | $(sed -n 's/.*"wall_s": \([0-9.]*\).*/\1/p' timing.json) s"
exit $RC
