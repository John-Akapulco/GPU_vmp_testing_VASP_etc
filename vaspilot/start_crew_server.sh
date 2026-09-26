#!/bin/bash
# Démarre l'interface web de VASPilot (agents CrewAI) sur http://localhost:51293
cd "$(dirname "$0")" && source env.sh
# ~/.vaspilot_llm définit LLM_MODEL, LLM_API_KEY et, pour une API compatible OpenAI, LLM_BASE_URL
[ -f "$HOME/.vaspilot_llm" ] || { echo "ERREUR : ~/.vaspilot_llm absent (voir README)"; exit 1; }
source "$HOME/.vaspilot_llm"
umask 077
sed -e "s|__LLM_MODEL__|$LLM_MODEL|" -e "s|__LLM_API_KEY__|$LLM_API_KEY|" \
    -e "s|__LLM_BASE_URL__|${LLM_BASE_URL:-null}|" configs/crew_config.template.yaml > configs/crew_config.yaml
nohup vaspilot_quart --config "$PWD/configs/crew_config.yaml" --host 127.0.0.1 --port 51293 \
    --work-dir "$PWD/crew_server/work" --allow-path "$PWD" \
    --max-concurrent-tasks 1 --max-queue-size 10 > crew_server/server.log 2>&1 &
echo $! > crew_server/pid.txt
echo "Serveur web démarré (PID $(cat crew_server/pid.txt)) : http://localhost:51293, journal : crew_server/server.log"
