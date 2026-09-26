#!/bin/bash
# Arrête les deux serveurs VASPilot
cd "$(dirname "$0")"
for f in mcp/pid.txt crew_server/pid.txt; do
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null && echo "arrêté : $f" && rm -f "$f"
done
