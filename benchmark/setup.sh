#!/bin/bash
# Installe l'environnement Python du benchmark (pymatgen, mp-api, matplotlib) dans benchmark/.venv
# et relie les POTCAR de /opt/vasp/POTCAR (jeu PBE_54) pour pymatgen.
set -e
cd "$(dirname "$0")"
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python - -q
.venv/bin/pip install -q "pymatgen>=2025.1" mp-api matplotlib pandas
mkdir -p psp && ln -sfn /opt/vasp/POTCAR psp/POT_GGA_PAW_PBE_54
echo "Prêt : source $(pwd)/env.sh"
