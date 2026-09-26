# Environnement VASPilot sur vm1-ic2mp : source env.sh
VASPILOT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VASPILOT_HOME
source "$HOME/testing_VASP/VASPilot/.venv/bin/activate"
# POTCAR de /opt/vasp/POTCAR (jeu PBE_54), vus par pymatgen via un lien POT_GGA_PAW_PBE_54
export PMG_VASP_PSP_DIR="$VASPILOT_HOME/psp"
export PMG_DEFAULT_FUNCTIONAL=PBE_54
