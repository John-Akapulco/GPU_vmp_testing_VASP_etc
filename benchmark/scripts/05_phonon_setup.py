#!/usr/bin/env python
"""Prépare les campagnes de phonons PBE (différences finies, phonopy) du benchmark.

Pour chaque matériau (maille primitive) et chaque variante GPU, crée
phonons/<label>/<variante>/ avec :
  relax1/     entrées de la relaxation serrée de la maille primitive (ISIF 3)
  phonon.json paramètres de la chaîne (supercellules, k, INCAR statique, NAC…)
La suite (relax2, born, sc<N>/disp-XXX, post-traitement) est générée et enchaînée
par scripts/phonon_pipeline.py dans un seul job Slurm par matériau et variante.

Les entrées sont strictement identiques entre variantes (KPAR = 1 partout) :
seul le nombre de GPU change, ce qui isole l'effet du matériel.

Usage :
    python scripts/05_phonon_setup.py                         # Si, Al, MgO ; g1 et g2k1
    python scripts/05_phonon_setup.py --labels scal_Si_002 --variants g1
    python scripts/05_phonon_setup.py --submit g1             # liste + sbatch (à ne faire qu'après accord)
    python scripts/05_phonon_setup.py --submit g2k1 --after 1234
"""
import argparse
import csv
import json
import math
import os
import subprocess
import time
import warnings
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.io.vasp import Kpoints
from pymatgen.io.vasp.sets import MPStaticSet

warnings.filterwarnings("ignore", message=".*POTCAR.*")
warnings.filterwarnings("ignore", category=UserWarning)

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
PH = BENCH / "phonons"
VARIANTS = {"g1": 1, "g2k1": 2}  # nom -> nGPU ; KPAR = 1 dans les deux cas

# Supercellules diagonales N×N×N de la maille primitive : deux tailles par matériau
# pour contrôler la convergence des fréquences (Si, MgO : 54 et 128 atomes ; Al : 64 et 125).
DEFAULT_SUPERCELLS = {"scal_Si_002": [3, 4], "scal_Al_001": [4, 5], "scal_MgO_002": [3, 4]}

# Densité de points k : grille centrée en Gamma, N_i = max(1, round(L·|b_i|)) (« Auto length » de VASP)
KLEN_GAP, KLEN_METAL = 50.0, 80.0

# Paramètres communs à tous les calculs VASP de la chaîne (s'ajoutent à ceux de MPStaticSet)
COMMON = {
    "PREC": "Accurate", "ENCUT": 520, "EDIFF": 1e-8, "ADDGRID": True, "LREAL": False,
    "LASPH": True, "ALGO": "Normal", "NELM": 200, "KPAR": 1,
    "LWAVE": False, "LCHARG": False, "LAECHG": False, "LVTOT": False, "LVHAR": False, "LELF": False,
    "LORBIT": None, "NCORE": None, "NPAR": None, "NEDOS": None, "ICHARG": None,
    "LMIXTAU": None, "KSPACING": None,
}
SMEAR_GAP = {"ISMEAR": 0, "SIGMA": 0.01}
SMEAR_METAL = {"ISMEAR": 1, "SIGMA": 0.2}
RELAX = {"IBRION": 2, "ISIF": 3, "NSW": 100, "EDIFFG": -1e-3}
STATIC = {"IBRION": -1, "NSW": 0, "ISIF": 2}
BORN = {"LEPSILON": True}


def kmesh_recip(structure, length):
    rec = structure.lattice.reciprocal_lattice_crystallographic.abc
    return [max(1, int(round(length * b))) for b in rec]


def incar_for(structure, gap, magnetic, extra):
    smear = SMEAR_METAL if gap <= 0 else SMEAR_GAP
    user = dict(COMMON, **smear, **extra)
    if not magnetic:
        user.update(ISPIN=1, MAGMOM=None)
    vis = MPStaticSet(structure, user_incar_settings=user,
                      user_potcar_functional="PBE_54", user_potcar_settings={"W": "W_sv"})
    return vis


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", nargs="+", default=list(DEFAULT_SUPERCELLS))
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
    ap.add_argument("--supercells", nargs="+", type=int, help="tailles N (N×N×N) ; défaut selon le matériau")
    ap.add_argument("--distance", type=float, default=0.01, help="amplitude des déplacements, Å")
    ap.add_argument("--force", action="store_true", help="réécrire une chaîne déjà commencée")
    ap.add_argument("--submit", nargs="+", choices=list(VARIANTS), help="soumettre ces variantes")
    ap.add_argument("--after", help="job Slurm dont la fin conditionne le démarrage (afterany)")
    ap.add_argument("--dry-run", action="store_true", help="avec --submit : afficher sans soumettre")
    args = ap.parse_args()

    with (BENCH / "structures" / "manifest.csv").open() as f:
        manifest = {r["label"]: r for r in csv.DictReader(f)}

    for label in args.labels:
        r = manifest[label]
        s = Structure.from_file(BENCH / "structures" / f"{label}.json")
        gap = float(r["band_gap_eV"])
        magnetic = r["is_magnetic"] == "True"
        polar = gap > 0 and len(s.composition) > 1
        klen = KLEN_METAL if gap <= 0 else KLEN_GAP
        sizes = args.supercells or DEFAULT_SUPERCELLS.get(label, [2])
        for var in args.variants:
            d = PH / label / var
            if (d / "relax1" / "OUTCAR").exists() and not args.force:
                print(f"{d.relative_to(BENCH)} : déjà commencé, inchangé")
                continue
            vis = incar_for(s, gap, magnetic, RELAX)
            vis.write_input(d / "relax1", potcar_spec=False)
            Kpoints.gamma_automatic(kmesh_recip(s, klen)).write_file(d / "relax1" / "KPOINTS")
            static = {k: v for k, v in incar_for(s, gap, magnetic, STATIC).incar.items()}
            meta = {
                "label": label, "material_id": r["material_id"], "formula": r["formula"],
                "nsites_prim": len(s), "band_gap_eV": gap, "is_metal": gap <= 0, "magnetic": magnetic,
                "polar": polar, "variant": var, "ngpu": VARIANTS[var], "kpar": 1,
                "k_length": klen, "supercells": sizes, "distance": args.distance,
                "incar_static": static, "born": BORN if polar else None,
                "mesh_dos": [30, 30, 30], "exe": "vasp_std",
            }
            (d / "phonon.json").write_text(json.dumps(meta, indent=1, default=str))
            print(f"{d.relative_to(BENCH)} : {len(s)} at., k relax {kmesh_recip(s, klen)}, "
                  f"supercellules {[f'{n}³={n**3 * len(s)} at.' for n in sizes]}, NAC {polar}")

    if not args.submit:
        return
    (BENCH / "lists").mkdir(exist_ok=True)
    (BENCH / "logs").mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    after = args.after
    for var in args.submit:  # variantes enchaînées : chacune attend la fin de la précédente
        dirs = sorted((str(PH / l / var) for l in args.labels),
                      key=lambda p: json.loads((Path(p) / "phonon.json").read_text())["nsites_prim"])
        lst = BENCH / "lists" / f"phonons_{var}_{stamp}.txt"
        lst.write_text("\n".join(dirs) + "\n")
        ngpu = VARIANTS[var]
        cmd = ["sbatch", f"--array=0-{len(dirs) - 1}%1", f"--gres=gpu:{ngpu}", f"--ntasks={ngpu}",
               f"--job-name=vph-{var}"] + ([f"--dependency=afterany:{after}"] if after else []) + \
              [str(BENCH / "run_phonon.slurm"), str(lst)]
        print(" ".join(cmd))
        if args.dry_run:
            after = after or "<job précédent>"
        else:
            out = subprocess.run(cmd, cwd=BENCH, capture_output=True, text=True, check=True).stdout.strip()
            print(out)
            after = out.split()[-1]


if __name__ == "__main__":
    main()
