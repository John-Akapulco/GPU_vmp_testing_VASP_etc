# Environnement du benchmark : source env.sh
BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BENCH_DIR
source "$BENCH_DIR/.venv/bin/activate"
# pymatgen cherche $PMG_VASP_PSP_DIR/POT_GGA_PAW_PBE_54/<symbole>/POTCAR
export PMG_VASP_PSP_DIR="$BENCH_DIR/psp"
# Clé API Materials Project : https://next-gen.materialsproject.org/api (compte gratuit)
# À placer dans ~/.mp_api_key (chmod 600) ou à exporter dans MP_API_KEY.
if [ -z "$MP_API_KEY" ] && [ -f "$HOME/.mp_api_key" ]; then
  MP_API_KEY="$(tr -d '[:space:]' < "$HOME/.mp_api_key")"
  export MP_API_KEY
fi
