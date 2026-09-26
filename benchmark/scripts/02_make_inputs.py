#!/usr/bin/env python
"""Génère les entrées VASP du benchmark avec les jeux de paramètres de Materials Project.

Méthodologies (pymatgen) :
  pbe    sp    -> MPStaticSet      (GGA/GGA+U, ENCUT 520, k : 100 pts/Å⁻³ réciproque)
  pbe    relax -> MPRelaxSet       (GGA/GGA+U, ENCUT 520, ISIF 3, k : 64 pts/Å⁻³ réciproque)
  r2scan sp    -> MPScanStaticSet  (METAGGA R2SCAN, ENCUT 680, KSPACING selon le gap MP)
  r2scan relax -> MPScanRelaxSet   (METAGGA R2SCAN, ENCUT 680, ISIF 3, EDIFFG -0.02)

Écarts assumés à la méthodologie MP (identiques pour tous les calculs) :
  - POTCAR : jeu PBE_54 de /opt/vasp/POTCAR aussi pour PBE (MP utilise l'ancien jeu « PBE »,
    identique pour presque tous les éléments) ; W_pv remplacé par W_sv ;
  - pas d'écriture de WAVECAR, CHGCAR, AECCAR, LOCPOT, ELFCAR (on mesure le calcul, pas les E/S) ;
  - NCORE retiré (imposé à 1 par la version GPU), KPAR fixé par la variante ;
  - ISMEAR=-5 (tétraèdres) remplacé par ISMEAR=0, SIGMA=0.05 quand la grille a moins de
    4 points k (VASP s'arrêterait ; c'est aussi la correction appliquée par custodian chez MP).

Variantes de parallélisation :
  g1    1 GPU, KPAR=1
  g2k1  2 GPU, KPAR=1  (bandes et ondes planes réparties sur les 2 GPU)
  g2k2  2 GPU, KPAR=2  (un groupe de points k par GPU ; ignoré si moins de 2 points k)

Arborescence produite : runs/<fonctionnelle>_<calcul>/<label>/<variante>/

Usage :
    python scripts/02_make_inputs.py                         # tout, variante g1
    python scripts/02_make_inputs.py --variants g1 g2k1 g2k2 --family scaling
    python scripts/02_make_inputs.py --func r2scan --calc sp --max-atoms 32
"""
import argparse
import csv
import json
import math
import os
import warnings
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.io.vasp.sets import MPRelaxSet, MPScanRelaxSet, MPScanStaticSet, MPStaticSet
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

warnings.filterwarnings("ignore", message=".*POTCAR.*")
warnings.filterwarnings("ignore", category=UserWarning)

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))

SETS = {
    ("pbe", "sp"): MPStaticSet,
    ("pbe", "relax"): MPRelaxSet,
    ("r2scan", "sp"): MPScanStaticSet,
    ("r2scan", "relax"): MPScanRelaxSet,
}
VARIANTS = {"g1": (1, 1), "g2k1": (2, 1), "g2k2": (2, 2)}  # nom -> (nGPU, KPAR)

BENCH_INCAR = {
    "LWAVE": False, "LCHARG": False, "LAECHG": False, "LVTOT": False, "LVHAR": False, "LELF": False,
    "NCORE": None, "NPAR": None,
}


def n_ir_kpoints(structure, incar, kpoints):
    """Estime le nombre de points k irréductibles (symétrie cristalline seule)."""
    if kpoints is not None:
        mesh, shift = kpoints.kpts[0], (0, 0, 0)
        if kpoints.style.name == "Monkhorst":
            shift = tuple(0 if m % 2 else 1 for m in mesh)
    else:  # KSPACING, grille centrée en Gamma (KGAMMA=.TRUE. par défaut)
        ks = incar["KSPACING"]
        mesh = [max(1, math.ceil(b / ks)) for b in structure.lattice.reciprocal_lattice.abc]
        shift = (0, 0, 0)
    try:
        return len(SpacegroupAnalyzer(structure, symprec=0.1).get_ir_reciprocal_mesh(mesh, shift)), list(mesh)
    except Exception:
        return math.prod(mesh), list(mesh)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--func", nargs="+", default=["pbe", "r2scan"], choices=["pbe", "r2scan"])
    ap.add_argument("--calc", nargs="+", default=["sp", "relax"], choices=["sp", "relax"])
    ap.add_argument("--variants", nargs="+", default=["g1"], choices=list(VARIANTS))
    ap.add_argument("--family", nargs="+", default=["scaling", "diversity", "user"])
    ap.add_argument("--labels", nargs="*", help="restreindre à ces labels")
    ap.add_argument("--max-atoms", type=int, default=80)
    ap.add_argument("--nsw", type=int, help="plafonner NSW pour les relaxations (défaut MP : 99)")
    ap.add_argument("--force", action="store_true", help="réécrire un dossier déjà lancé")
    args = ap.parse_args()

    with (BENCH / "structures" / "manifest.csv").open() as f:
        manifest = [r for r in csv.DictReader(f)
                    if r["family"] in args.family and int(r["nsites"]) <= args.max_atoms
                    and (not args.labels or r["label"] in args.labels)]

    n_new = n_skip = 0
    for r in sorted(manifest, key=lambda r: int(r["nsites"])):
        structure = Structure.from_file(BENCH / "structures" / f"{r['label']}.json")
        gap = float(r["band_gap_eV"])
        for func in args.func:
            for calc in args.calc:
                kwargs = dict(user_potcar_functional="PBE_54", user_potcar_settings={"W": "W_sv"})
                if func == "r2scan":
                    kwargs["bandgap"] = gap  # KSPACING interpolé entre 0.22 et 0.44 comme MP
                for var in args.variants:
                    ngpu, kpar = VARIANTS[var]
                    d = BENCH / "runs" / f"{func}_{calc}" / r["label"] / var
                    if (d / "OUTCAR").exists() and not args.force:
                        n_skip += 1
                        continue
                    incar_user = dict(BENCH_INCAR, KPAR=kpar)
                    if calc == "relax" and args.nsw:
                        incar_user["NSW"] = args.nsw
                    vis = SETS[(func, calc)](structure, user_incar_settings=incar_user, **kwargs)
                    nk, mesh = n_ir_kpoints(vis.structure, vis.incar, vis.kpoints)
                    if kpar > 1 and nk < kpar:
                        continue
                    if vis.incar.get("ISMEAR") == -5 and math.prod(mesh) < 4:
                        vis = SETS[(func, calc)](structure, **kwargs, user_incar_settings=dict(
                            incar_user, ISMEAR=0, SIGMA=0.05))
                    vis.write_input(d, potcar_spec=False)
                    meta = {
                        "label": r["label"], "family": r["family"], "material_id": r["material_id"],
                        "formula": r["formula"], "nsites": len(vis.structure), "func": func, "calc": calc,
                        "variant": var, "ngpu": ngpu, "kpar": kpar, "input_set": type(vis).__name__,
                        "band_gap_eV": gap, "kmesh": mesh, "n_ir_kpoints_est": nk,
                        "mp_uncorrected_energy_per_atom": r.get(f"mp_E_{func}") or None,
                        "exe": "vasp_std",
                    }
                    (d / "bench.json").write_text(json.dumps(meta, indent=1))
                    n_new += 1
    print(f"{n_new} dossiers générés, {n_skip} déjà lancés (inchangés). Racine : {BENCH / 'runs'}")


if __name__ == "__main__":
    main()
