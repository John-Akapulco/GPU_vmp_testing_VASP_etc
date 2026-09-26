#!/bin/bash
# Démarre le serveur MCP de VASPilot (outils VASP, structures, Slurm) sur le port 8933.
cd "$(dirname "$0")" && source env.sh
[ -f "$HOME/.mp_api_key" ] || { echo "ERREUR : clé Materials Project absente (~/.mp_api_key)"; exit 1; }
umask 077
sed "s|__MP_API_KEY__|$(tr -d '[:space:]' < "$HOME/.mp_api_key")|" configs/mcp_config.template.yaml > configs/mcp_config.yaml
nohup vaspilot_mcp --config "$PWD/configs/mcp_config.yaml" --port 8933 --host 127.0.0.1 > mcp/mcp.log 2>&1 &
echo $! > mcp/pid.txt
echo "Serveur MCP démarré (PID $(cat mcp/pid.txt)), journal : mcp/mcp.log"
