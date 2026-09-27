#!/usr/bin/env python
"""Enchaîne les segments d'une dynamique moléculaire VASP (avec ou sans MLFF) dans un job Slurm.

Segment k (seg-XX/) : entrées de input/, POSCAR = CONTCAR du segment précédent (positions et
vitesses), ML_AB = ML_ABN du segment précédent, TEBEG/TEEND = portion de la rampe de température.
Un segment terminé n'est pas relancé : la chaîne peut être reprise ou prolongée.

Relevés par segment (seg-XX/timing.json et bilan dans aimd_run.json) : temps mur, nombre de pas,
temps moyen par pas, pas DFT et pas prédits (MLFF, d'après ML_LOGFILE), pas SCF par pas DFT,
mémoire et utilisation GPU (gpu_monitor.csv).

Usage (dans run_aimd.slurm) :  python scripts/aimd_pipeline.py runs_aimd/<nom> [segment max]
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pymatgen.io.vasp import Incar


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def done(d):
    o = d / "OUTCAR"
    return o.exists() and "General timing" in o.read_text(errors="ignore")[-20000:]


def seg_temps(meta, k):
    t0, t1, n = meta["t_start"], meta["t_end"], meta["nseg"]
    return t0 + (t1 - t0) * (k - 1) / n, t0 + (t1 - t0) * k / n


def prepare(root, meta, k):
    d = root / f"seg-{k:02d}"
    if (d / "INCAR").exists():
        return d
    d.mkdir()
    for f in ("POTCAR", "KPOINTS"):
        shutil.copy(root / "input" / f, d / f)
    prev = root / f"seg-{k - 1:02d}"
    shutil.copy(prev / "CONTCAR" if k > 1 else root / "input" / "POSCAR", d / "POSCAR")
    if meta["ml"] and k > 1:
        shutil.copy(prev / "ML_ABN", d / "ML_AB")
    incar = Incar.from_file(root / "input" / "INCAR")
    tb, te = seg_temps(meta, k)
    incar.update(NSW=meta["steps_per_seg"], TEBEG=round(tb, 1), TEEND=round(te, 1))
    incar.write_file(d / "INCAR")
    return d


def stats(d, meta):
    """Pas effectués, pas DFT et pas SCF, d'après OSZICAR et ML_LOGFILE."""
    osz = (d / "OSZICAR").read_text(errors="ignore") if (d / "OSZICAR").exists() else ""
    blocks = re.split(r"^\s*\d+\s+T=", osz, flags=re.M)
    n_steps = len(blocks) - 1
    scf = [len(re.findall(r"^(?:DAV|RMM|CG|DIA):", b, re.M)) for b in blocks[:-1]]
    dft = [x for x in scf if x > 0]
    out = {"n_steps": n_steps, "n_dft_steps": len(dft),
           "scf_per_dft_step": round(sum(dft) / len(dft), 2) if dft else None}
    log = d / "ML_LOGFILE"
    if meta["ml"] and log.exists():
        states = re.findall(r"^STATUS\s+\d+\s+(\S+)", log.read_text(errors="ignore"), re.M)
        out["ml_status"] = {s: states.count(s) for s in sorted(set(states))}
    return out


def run(d, meta):
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
    t = {"segment": d.name, "ngpu": ngpu, "omp": int(os.environ.get("OMP_NUM_THREADS", 1)),
         "start": start, "end": now(), "wall_s": round(wall, 2), "return_code": rc,
         "slurm_job": os.environ.get("SLURM_JOB_ID", "")}
    t.update(stats(d, meta))
    t["s_per_step"] = round(wall / t["n_steps"], 3) if t["n_steps"] else None
    (d / "timing.json").write_text(json.dumps(t, indent=1))
    print(f"{now()} | {d.name} | code {rc} | {wall:9.1f} s | {t['n_steps']} pas, "
          f"{t['n_dft_steps']} DFT | {t['s_per_step']} s/pas | {t.get('ml_status', '')}", flush=True)
    return t


def main():
    root = Path(sys.argv[1]).resolve()
    meta = json.loads((root / "aimd.json").read_text())
    max_seg = min(int(sys.argv[2]) if len(sys.argv) > 2 else meta["nseg"], meta["nseg"])
    rj = root / "aimd_run.json"
    state = json.loads(rj.read_text()) if rj.exists() else {"runs": []}
    run_log = {"start": now(), "slurm_job": os.environ.get("SLURM_JOB_ID"), "segments": []}
    state["runs"].append(run_log)
    status = "ok"
    for k in range(1, max_seg + 1):
        d = prepare(root, meta, k)
        if done(d):
            continue
        t = run(d, meta)
        run_log["segments"].append(t)
        if t["return_code"] != 0 or not done(d):
            status = f"erreur au segment {k}"
            break
    run_log["end"], run_log["status"] = now(), status
    rj.write_text(json.dumps(state, indent=1))
    print(f"{now()} | fin {root.name} | {status}", flush=True)
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
