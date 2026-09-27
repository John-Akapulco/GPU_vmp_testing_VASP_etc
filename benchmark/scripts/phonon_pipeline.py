#!/usr/bin/env python
"""Chaîne complète de phonons pour un matériau et une variante, exécutée dans un job Slurm.

Étapes (chacune ignorée si déjà terminée, la chaîne peut donc être relancée) :
  1. relax1, relax2, … : relaxation serrée de la maille primitive (ISIF 3), relancée depuis
     le CONTCAR tant qu'une passe fait plus de 2 pas ioniques (base d'ondes planes remise à jour)
  2. born (matériaux polaires) : charges de Born et ε∞ par LEPSILON ; si VASP refuse,
     born_lcalceps par LCALCEPS (champ fini)
  3. pour chaque supercellule N×N×N : déplacements phonopy, calcul des forces (disp-XXX),
     constantes de force, NAC, bandes, DOS, grandeurs thermodynamiques
Temps de chaque étape dans <étape>/timing.json ; bilan de la chaîne dans pipeline.json.

Usage (dans run_phonon.slurm) :  python scripts/phonon_pipeline.py phonons/<label>/<variante>
Post-traitement seul :           python scripts/phonon_pipeline.py <dossier> --post-only
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from phonopy import Phonopy
from phonopy.phonon.band_structure import get_band_qpoints_by_seekpath
from phonopy.interface.calculator import get_calculator_physical_units
from phonopy.interface.vasp import read_vasp, read_vasprun_calculation, write_vasp
from pymatgen.core import Structure
from pymatgen.io.vasp import Incar, Kpoints, Outcar

MAX_RELAX_PASSES = 4
# Maille déjà primitive : primitive_matrix="P". Moteur C de phonopy : le moteur Rust (phonors 0.5)
# installé est incompatible avec phonopy 4.6 (grid_index_from_address absent).

ROOT = None  # dossier de la chaîne, fixé dans main()


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def done(d):
    o = d / "OUTCAR"
    return o.exists() and "General timing" in o.read_text(errors="ignore")[-20000:]


def run_vasp(d, meta, log):
    """Lance VASP dans d (sauf s'il est déjà terminé) et enregistre les temps comme run_bench.slurm."""
    if done(d):
        return json.loads((d / "timing.json").read_text()) if (d / "timing.json").exists() else {}
    ngpu = int(os.environ.get("SLURM_NTASKS", meta["ngpu"]))
    mon = subprocess.Popen(["nvidia-smi", "--query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw",
                            "--format=csv,noheader,nounits", "-l", "2"],
                           stdout=open(d / "gpu_monitor.csv", "w"), stderr=subprocess.DEVNULL)
    t0, start = time.time(), now()
    with open(d / "vasp.stdout", "w") as out:
        rc = subprocess.run(["mpirun", "-np", str(ngpu), "--bind-to", "none", meta["exe"]],
                            cwd=d, stdout=out, stderr=subprocess.STDOUT).returncode
    wall = time.time() - t0
    mon.terminate()
    t = {"step": str(d.relative_to(ROOT)), "ngpu": ngpu,
         "gpus": os.environ.get("CUDA_VISIBLE_DEVICES", ""), "start": start, "end": now(),
         "wall_s": round(wall, 2), "return_code": rc,
         "slurm_job": f"{os.environ.get('SLURM_ARRAY_JOB_ID', '')}_{os.environ.get('SLURM_ARRAY_TASK_ID', '')}"}
    (d / "timing.json").write_text(json.dumps(t, indent=1))
    log.append(t)
    print(f"{now()} | {t['step']:<28} | code {rc} | {wall:9.1f} s", flush=True)
    return t


def prepare(d, src, poscar, incar, kpts):
    """Crée un dossier de calcul : POTCAR copié de src, INCAR et KPOINTS donnés."""
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "POTCAR", d / "POTCAR")
    if isinstance(poscar, Path):
        shutil.copy(poscar, d / "POSCAR")
    else:
        write_vasp(d / "POSCAR", poscar)
    Incar({k: v for k, v in incar.items() if v is not None}).write_file(d / "INCAR")
    Kpoints.gamma_automatic(kpts).write_file(d / "KPOINTS")


def kmesh(structure, length):
    rec = structure.lattice.reciprocal_lattice_crystallographic.abc
    return [max(1, int(round(length * b))) for b in rec]


def check_species_order(poscar, potcar):
    """Vérifie que l'ordre des espèces du POSCAR suit celui du POTCAR (sinon forces fausses)."""
    species = open(poscar).read().splitlines()[5].split()
    titles = [l.split()[3].split("_")[0] for l in open(potcar) if re.match(r"^\s*TITEL", l)]
    if species != titles:
        raise RuntimeError(f"{poscar} : espèces {species} ≠ POTCAR {titles}")


def relax(root, meta, log):
    """Passes de relaxation successives ; renvoie le dossier de la dernière passe."""
    for k in range(1, MAX_RELAX_PASSES + 1):
        d = root / f"relax{k}"
        if k > 1 and not (d / "INCAR").exists():
            prev = root / f"relax{k - 1}"
            d.mkdir()
            for f in ("INCAR", "KPOINTS", "POTCAR"):
                shutil.copy(prev / f, d / f)
            shutil.copy(prev / "CONTCAR", d / "POSCAR")
        t = run_vasp(d, meta, log)
        if t.get("return_code", 0) != 0 or not done(d):
            raise RuntimeError(f"{d} : VASP en erreur")
        text = (d / "OUTCAR").read_text(errors="ignore")
        n_ionic = len(re.findall(r"^\s*POSITION\s+TOTAL-FORCE", text, re.M))
        if "reached required accuracy" in text and n_ionic <= 2:
            return d
    raise RuntimeError(f"relaxation non convergée après {MAX_RELAX_PASSES} passes")


def born(root, relaxed, meta, log):
    """Charges de Born et ε∞ ; LEPSILON, puis LCALCEPS si VASP échoue."""
    kpts = [int(x) for x in (relaxed / "KPOINTS").read_text().splitlines()[3].split()]
    for name, flags in (("born", {"LEPSILON": True}), ("born_lcalceps", {"LCALCEPS": True})):
        d = root / name
        if not (d / "INCAR").exists():
            prepare(d, relaxed, relaxed / "CONTCAR", dict(meta["incar_static"], **flags), kpts)
        t = run_vasp(d, meta, log)
        if t.get("return_code", 0) == 0 and done(d):
            if name == "born":
                o = Outcar(d / "OUTCAR")
                o.read_lepsilon()
                borns, eps = np.array(o.born), np.array(o.dielectric_tensor)
            else:
                borns, eps = read_born_lcalceps(d / "OUTCAR")
            return {"born": borns.tolist(), "dielectric": eps.tolist(), "method": name}
        print(f"{name} en échec, méthode suivante", flush=True)
    raise RuntimeError("charges de Born non obtenues")


def read_born_lcalceps(outcar):
    """Lit ε∞ et les charges de Born d'un calcul LCALCEPS (dernier bloc de l'OUTCAR)."""
    text = outcar.read_text(errors="ignore")
    m = list(re.finditer(r"MACROSCOPIC STATIC DIELECTRIC TENSOR \(including local field effects in DFT\)"
                         r"\s*\n\s*-+\n((?:.*\n){3})", text))
    eps = np.array([[float(x) for x in l.split()] for l in m[-1].group(1).splitlines()])
    blk = text[text.rfind("BORN EFFECTIVE CHARGES"):].split("\n")
    borns, cur = [], []
    for l in blk[2:]:
        if l.strip().startswith("ion"):
            cur = []
        elif re.match(r"^\s+[123]\s", l):
            cur.append([float(x) for x in l.split()[1:4]])
            if len(cur) == 3:
                borns.append(cur)
        elif l.strip().startswith("---") or not l.strip():
            if borns:
                break
    return np.array(borns), eps


def phonons(root, relaxed, n, meta, nac, log, post_only=False):
    d = root / f"sc{n}"
    unit = read_vasp(relaxed / "CONTCAR")
    if not (d / "phonopy_disp.yaml").exists():
        t0 = time.time()
        ph = Phonopy(unit, supercell_matrix=np.eye(3, dtype=int) * n, primitive_matrix="P", lang="C")
        ph.generate_displacements(distance=meta["distance"])
        d.mkdir(parents=True, exist_ok=True)
        ph.save(d / "phonopy_disp.yaml")
        sc0 = Structure.from_file(relaxed / "CONTCAR").make_supercell([n, n, n], in_place=False)
        kpts = kmesh(sc0, meta["k_length"])
        for i, cell in enumerate(ph.supercells_with_displacements, 1):
            prepare(d / f"disp-{i:03d}", relaxed, cell, meta["incar_static"], kpts)
            check_species_order(d / f"disp-{i:03d}" / "POSCAR", d / f"disp-{i:03d}" / "POTCAR")
        log.append({"step": f"sc{n}/displacements", "wall_s": round(time.time() - t0, 2), "start": now()})
    ph_disp = sorted(d.glob("disp-*"))
    if not post_only:
        for dd in ph_disp:
            t = run_vasp(dd, meta, log)
            if t.get("return_code", 0) != 0 or not done(dd):
                raise RuntimeError(f"{dd} : VASP en erreur")
    t0 = time.time()
    summary = post(d, ph_disp, nac, meta)
    summary.update(n=n, natoms_sc=len(unit.symbols) * n ** 3, n_disp=len(ph_disp),
                   kmesh_sc=[int(x) for x in (ph_disp[0] / "KPOINTS").read_text().splitlines()[3].split()])
    (d / "summary.json").write_text(json.dumps(summary, indent=1))
    log.append({"step": f"sc{n}/post", "wall_s": round(time.time() - t0, 2), "start": now()})
    return summary


def post(d, disp_dirs, nac, meta):
    """Constantes de force, NAC, bandes, DOS, thermodynamique et contrôles de qualité."""
    import phonopy
    ph = phonopy.load(d / "phonopy_disp.yaml", produce_fc=False, is_nac=False, lang="C")
    forces, drift = [], []
    for dd in disp_dirs:
        _, _, f, _ = read_vasprun_calculation(dd / "vasprun.xml")
        drift.append(float(np.abs(f.sum(axis=0)).max()))
        forces.append(f)
    ph.forces = np.array(forces)
    ph.produce_force_constants()
    ph.symmetrize_force_constants()
    if nac:
        ph.nac_params = {"born": np.array(nac["born"]), "dielectric": np.array(nac["dielectric"]),
                         "factor": get_calculator_physical_units("vasp").nac_factor}
    ph.save(d / "phonopy_params.yaml", settings={"force_constants": True})

    paths, labels, conn = get_band_qpoints_by_seekpath(ph.primitive, 101)
    ph.run_band_structure(paths, path_connections=conn, labels=labels)
    ph.write_yaml_band_structure(filename=d / "band.yaml")
    segs = ph.get_band_structure_dict()["frequencies"]
    ph.run_mesh(meta["mesh_dos"], is_gamma_center=True)
    mesh = ph.get_mesh_dict()
    q, fr = np.array(mesh["qpoints"]), np.array(mesh["frequencies"])
    off_gamma = np.linalg.norm(q, axis=1) > 1e-8
    ph.run_total_dos()
    ph.write_total_dos(filename=d / "total_dos.dat")
    ph.run_thermal_properties(t_min=0, t_max=1000, t_step=10)
    tp = ph.get_thermal_properties_dict()
    i300 = int(np.argmin(np.abs(np.array(tp["temperatures"]) - 300)))
    ph.plot_band_structure_and_dos().savefig(d / "band_dos.png", dpi=150)

    # Fréquences aux points spéciaux (extrémités des segments du chemin seekpath) ;
    # à Γ, avec NAC, la valeur dépend de la direction d'approche : on garde la première
    labels = [re.sub(r"[$]|\\mathrm\{|\}", "", l).replace("\\Gamma", "Γ") for l in labels]
    special, li = {}, 0
    for seg, c in zip(segs, conn):
        special.setdefault(labels[li], np.round(seg[0], 4).tolist())
        special.setdefault(labels[li + 1], np.round(seg[-1], 4).tolist())
        li += 1 if c else 2
    return {
        "fmin_mesh_THz": float(fr[off_gamma].min()),
        "fmax_mesh_THz": float(fr.max()),
        "fmin_band_THz": float(min(np.min(s) for s in segs)),
        "acoustic_gamma_THz": sorted(np.round(ph.get_frequencies([0, 0, 0])[:3], 4).tolist()),
        "max_force_drift_eV_A": max(drift),
        "special_points_THz": special,
        "F_300K_kJ_mol": float(tp["free_energy"][i300]),
        "S_300K_J_K_mol": float(tp["entropy"][i300]),
        "Cv_300K_J_K_mol": float(tp["heat_capacity"][i300]),
        "nac": bool(nac),
    }


def slurm_submit_time():
    job = os.environ.get("SLURM_ARRAY_JOB_ID")
    job = f"{job}_{os.environ['SLURM_ARRAY_TASK_ID']}" if job else os.environ.get("SLURM_JOB_ID")
    if not job:
        return None
    out = subprocess.run(["scontrol", "show", "job", job], capture_output=True, text=True).stdout
    m = re.search(r"SubmitTime=(\S+)", out)
    return m.group(1) if m else None


def main():
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--post-only", action="store_true")
    args = ap.parse_args()
    ROOT = root = Path(args.dir).resolve()
    meta = json.loads((root / "phonon.json").read_text())
    pj = root / "pipeline.json"
    state = json.loads(pj.read_text()) if pj.exists() else {"runs": []}
    run = {"submit": slurm_submit_time(), "start": now(), "node": os.environ.get("SLURMD_NODENAME"),
           "slurm_job": f"{os.environ.get('SLURM_ARRAY_JOB_ID', '')}_{os.environ.get('SLURM_ARRAY_TASK_ID', '')}",
           "steps": []}
    state["runs"].append(run)
    log = run["steps"]
    status = "ok"
    try:
        relaxed = relax(root, meta, log)
        s = Structure.from_file(relaxed / "CONTCAR")
        state["relaxed"] = {"dir": relaxed.name, "abc": s.lattice.abc, "angles": s.lattice.angles,
                            "volume": s.volume}
        nac = born(root, relaxed, meta, log) if meta["polar"] else None
        state["nac"] = nac
        state["supercells"] = {}
        for n in meta["supercells"]:
            state["supercells"][str(n)] = phonons(root, relaxed, n, meta, nac, log, args.post_only)
    except Exception as e:
        status = f"erreur : {e}"
        print(status, file=sys.stderr, flush=True)
    run["end"], run["status"] = now(), status
    pj.write_text(json.dumps(state, indent=1))
    print(f"{now()} | fin de la chaîne {root.name} | {status}", flush=True)
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
