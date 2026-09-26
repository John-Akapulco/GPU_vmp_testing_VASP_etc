#!/bin/bash
# Script copié par VASPilot dans chaque dossier de calcul puis soumis par sbatch.
# VASPilot ajoute --gres=gpu:N --ntasks=N (1 rang MPI par GPU H100).
#SBATCH --job-name=vaspilot
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --output=slurm-%j.out

source /opt/vasp/vasp_env.sh
export OMP_NUM_THREADS=1
export OMPI_MCA_coll_hcoll_enable=0   # pas d'InfiniBand
cd "$SLURM_SUBMIT_DIR"
rm -f slurm_exit_code

EXE=vasp_std
if grep -qiE '^[[:space:]]*(LSORBIT|LNONCOLLINEAR)[[:space:]]*=[[:space:]]*\.?T' INCAR; then EXE=vasp_ncl; fi

nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw \
           --format=csv,noheader,nounits -l 2 > gpu_monitor.csv 2>/dev/null &
MON=$!
T0=$(date +%s.%N)
echo "Début $(date -Is) | $EXE sur $SLURM_NTASKS GPU ($CUDA_VISIBLE_DEVICES) | job $SLURM_JOB_ID"
# VASPilot lit la sortie de VASP dans le fichier « log » pour diagnostiquer les erreurs
mpirun -np "$SLURM_NTASKS" --bind-to none "$EXE" > log 2>&1
RC=$?
T1=$(date +%s.%N)
kill $MON 2>/dev/null
echo "{\"slurm_job\": \"$SLURM_JOB_ID\", \"gpus\": \"$CUDA_VISIBLE_DEVICES\", \"ntasks\": $SLURM_NTASKS, \"return_code\": $RC, \"wall_s\": $(echo "$T1 - $T0" | bc)}" > timing.json
# Sans comptabilité Slurm (sacct désactivé), VASPilot lit ce fichier pour connaître l'issue du job
echo $RC > slurm_exit_code
echo "Fin $(date -Is) | code $RC"
exit $RC
