#!/usr/bin/env python
"""Prépare les dynamiques moléculaires ab initio (NVT) de LiN3 (mp-2659), avec ou sans MLFF.

Supercellule 2×3×3 de la maille conventionnelle (144 atomes : 36 Li, 108 N), point Γ seul
(vasp_gam), PBE+D3(BJ), polarisé en spin (ISPIN 2), ENCUT 520 eV, thermostat de Langevin,
pas de 1 fs, 1 GPU.

Calculs (runs_aimd/<nom>/) :
  calib_dft_gam_g1   AIMD pure, 200 pas à 300 K, vasp_gam, 1 GPU        coût d'un pas DFT
  calib_dft_std_g1   idem, vasp_std au point Γ                          gain de vasp_gam
  calib_ml_omp1_g1   MLFF train, 1er ps (300 → 313 K), OMP_NUM_THREADS=1 effet des threads CPU sur MLFF
  nvt_ml_g1          MLFF train, 15 ps de 300 à 500 K en 15 segments de 1 ps, OMP_NUM_THREADS=16
                     (étape 1 : segment 1 seul ; étape 2 : segments 2 à 15, après accord)

Chaque calcul contient aimd.json (paramètres) et est exécuté par scripts/aimd_pipeline.py.

Usage :
    python scripts/08_aimd_setup.py                    # structure + dossiers
    python scripts/08_aimd_setup.py --submit calib --dry-run
    python scripts/08_aimd_setup.py --submit calib --after 187     # étape 1 (après accord)
    python scripts/08_aimd_setup.py --submit production            # étape 2 (après accord)
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.io.vasp import Incar, Kpoints, Poscar, Potcar
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
AIMD = BENCH / "runs_aimd"
MPID, LABEL, SUPERCELL = "mp-2659", "LiN3_144", (2, 3, 3)
POTCARS = {"Li": "Li_sv", "N": "N"}  # choix de Materials Project

INCAR = {
    "SYSTEM": "LiN3 2x3x3 NVT",
    # électronique
    "PREC": "Normal", "ENCUT": 520, "EDIFF": 1e-6, "ALGO": "Fast", "LREAL": "Auto",
    "ISMEAR": 0, "SIGMA": 0.05, "ISPIN": 2,  # MAGMOM par défaut (1 µB par atome)
    "ISYM": 0, "NELMIN": 4, "NELM": 100,
    "IVDW": 12,  # DFT-D3(BJ)
    "KPAR": 1, "LWAVE": False, "LCHARG": False,
    # dynamique : NVT, thermostat de Langevin (recommandé pour l'entraînement MLFF)
    "IBRION": 0, "POTIM": 1.0, "MDALGO": 3, "ISIF": 2, "LANGEVIN_GAMMA": [10, 10],
    "RANDOM_SEED": [271828, 0, 0],
}
ML = {"ML_LMLFF": True, "ML_MODE": "train"}

# nom -> (ML, exe, nGPU, OMP, segments, pas par segment, T début, T fin, étape)
RUNS = {
    "calib_dft_gam_g1": (False, "vasp_gam", 1, 1, 1, 200, 300, 300, "calib"),
    "calib_dft_std_g1": (False, "vasp_std", 1, 1, 1, 200, 300, 300, "calib"),
    "calib_ml_omp1_g1": (True, "vasp_gam", 1, 1, 15, 1000, 300, 500, "calib"),
    "nvt_ml_g1": (True, "vasp_gam", 1, 16, 15, 1000, 300, 500, "production"),
}


def structure():
    f = BENCH / "structures" / f"aimd_{LABEL}.json"
    if f.exists():
        return Structure.from_file(f)
    from mp_api.client import MPRester
    key = Path("~/.mp_api_key").expanduser().read_text().strip()
    with MPRester(key) as m:
        prim = m.get_structure_by_material_id(MPID)
    s = SpacegroupAnalyzer(prim).get_conventional_standard_structure()
    s.make_supercell(SUPERCELL)
    s = s.get_sorted_structure(key=lambda site: list(POTCARS).index(site.specie.symbol))
    f.write_text(s.to_json())
    return s


def setup(force):
    s = structure()
    print(f"{LABEL} : {s.composition.reduced_formula} {len(s)} at., {s.composition}, "
          f"a b c = {tuple(round(x, 2) for x in s.lattice.abc)}, V = {s.volume:.0f} Å³")
    for name, (ml, exe, ngpu, omp, nseg, nsteps, t0, t1, stage) in RUNS.items():
        d = AIMD / name
        if (d / "seg-01" / "OUTCAR").exists() and not force:
            print(f"{d.relative_to(BENCH)} : déjà commencé, inchangé")
            continue
        (d / "input").mkdir(parents=True, exist_ok=True)
        Poscar(s).write_file(d / "input" / "POSCAR")
        Potcar([POTCARS[e] for e in POTCARS], functional="PBE_54").write_file(d / "input" / "POTCAR")
        Kpoints.gamma_automatic((1, 1, 1)).write_file(d / "input" / "KPOINTS")
        Incar(dict(INCAR, **(ML if ml else {}))).write_file(d / "input" / "INCAR")
        meta = {"name": name, "material_id": MPID, "label": LABEL, "natoms": len(s), "ml": ml, "exe": exe,
                "ngpu": ngpu, "omp": omp, "nseg": nseg, "steps_per_seg": nsteps,
                "t_start": t0, "t_end": t1, "potim_fs": INCAR["POTIM"], "stage": stage}
        (d / "aimd.json").write_text(json.dumps(meta, indent=1))
        ramp = f"{t0} → {t1} K" if t0 != t1 else f"{t0} K"
        print(f"  {name:<18} {'MLFF' if ml else 'DFT ':<4} {exe} {ngpu} GPU OMP={omp:<2} "
              f"{nseg} × {nsteps} pas, {ramp}")


def submit(stage, after, dry):
    names = [n for n, r in RUNS.items() if r[-1] == stage or (stage == "calib" and n == "nvt_ml_g1")]
    (BENCH / "logs").mkdir(exist_ok=True)
    for name in names:  # un job par calcul, chacun attend le précédent (une seule GPU à la fois)
        ml, exe, ngpu, omp, nseg, *_ = RUNS[name]
        max_seg = 1 if stage == "calib" and ml else nseg  # calibration MLFF : 1er segment seul
        cmd = ["sbatch", f"--gres=gpu:{ngpu}", f"--ntasks={ngpu}", f"--cpus-per-task={max(omp, 1)}",
               f"--job-name=vmd-{name}"] + ([f"--dependency=afterany:{after}"] if after else []) + \
              [str(BENCH / "run_aimd.slurm"), str(AIMD / name), str(max_seg)]
        print(" ".join(cmd))
        if dry:
            after = "<job précédent>"
        else:
            out = subprocess.run(cmd, cwd=BENCH, capture_output=True, text=True, check=True).stdout.strip()
            print(out)
            after = out.split()[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submit", choices=["calib", "production"])
    ap.add_argument("--after", help="job Slurm dont la fin conditionne le démarrage (afterany)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="réécrire un calcul déjà commencé")
    args = ap.parse_args()
    if args.submit:
        submit(args.submit, args.after, args.dry_run)
    else:
        setup(args.force)


if __name__ == "__main__":
    main()
