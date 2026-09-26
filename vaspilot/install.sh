#!/bin/bash
# Installe VASPilot adapté à vm1-ic2mp :
#  - clone de VASPilot au commit 2d9b2df (29/09/2025) + patch vaspilot-vm1.patch
#  - dépendances figées (requirements-lock.txt ; crewai 0.159, fastmcp 2.11, numpy 1.26),
#    les versions plus récentes de crewai ne sont plus compatibles avec VASPilot
#  - dossiers de travail et lien vers les POTCAR
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${VASPILOT_SRC:-$HOME/testing_VASP/VASPilot}"
if [ ! -d "$SRC/.git" ]; then
  git clone https://github.com/JiaxuanLiu-Arsko/VASPilot.git "$SRC"
fi
cd "$SRC"
if ! git rev-parse -q --verify vm1-ic2mp-gpu >/dev/null; then
  git checkout -q -b vm1-ic2mp-gpu 2d9b2df
  git -c user.name=vm1-ic2mp -c user.email=vm1-ic2mp@localhost am -q "$HERE/vaspilot-vm1.patch"
fi
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python - -q
.venv/bin/pip install -q uv
.venv/bin/uv pip install -q --python .venv/bin/python -r "$HERE/requirements-lock.txt"
.venv/bin/uv pip install -q --python .venv/bin/python --no-deps -e .
cd "$HERE"
mkdir -p psp mcp/{work,record,downloads,uploads} crew_server/work
ln -sfn /opt/vasp/POTCAR psp/POT_GGA_PAW_PBE_54
echo "VASPilot installé dans $SRC (branche vm1-ic2mp-gpu). Suite : voir vaspilot/README.md"
