#!/usr/bin/env python
"""Scan du paramètre NSIM de VASP GPU (OpenACC) sur une H100, un calcul à la fois.

NSIM (nombre de bandes optimisées ensemble) ne doit changer que le temps, pas l'énergie.
Pour comparer à travail égal, chaque calcul fait exactement 20 pas électroniques
(NELMIN = NELM = 20, EDIFF = 1E-10), en point simple, sans WAVECAR ni CHGCAR.

Systèmes (structures du dépôt, paramètres MPStaticSet : PBE, PBE_54, ENCUT 520 eV, ISPIN 2) :
  MgO_032   scal_MgO_032                  32 atomes, 12 points k
  LiN3_144  aimd_LiN3_144 (mp-2659)      144 atomes,  8 points k
  Si_216    scal_Si_008 × 3×3×3          216 atomes,  point Γ seul
  + HSE06 sur MgO_032 : LHFCALC, HFSCREEN 0,2, AEXX 0,25, ALGO Normal (RMM-DIIS exclu en hybride),
    NELM = NELMIN = 12 (4 pas d'initialisation + 8 pas hybrides, ~65 min par calcul)

Valeurs : NSIM = 4, 8, 16, 32, 64, 128 (ignorées si NSIM > NBANDS). 1 GPU, 1 rang MPI,
KPAR = 1, NCORE = 1, OMP_NUM_THREADS = 8. Une seule exécution par valeur (r1) : les répétitions r2
prévues au départ ont été abandonnées le 01/10/2026 (sur MgO_032 PBE, r1 et r2 différaient de moins de 1 %).

Arborescence : nsim_scan/<système>/<pbe|hse>/nsimXXX/rK/ ; essai : nsim_scan/_test/<système>_<fonct>_nsimXXX/

Usage :
    python scripts/09_nsim_scan.py setup --test            # essai : MgO_032, NSIM 4, PBE et HSE
    python scripts/09_nsim_scan.py submit --test [--after JOB] [--dry-run]
    python scripts/09_nsim_scan.py setup                   # scan, dossiers r1
    python scripts/09_nsim_scan.py submit [--after JOB] [--only pbe|hse] [--nsim 8 16 ...] [--dry-run]
    python scripts/09_nsim_scan.py analyze                 # CSV, figures, rapport (r1 seulement)
"""
import argparse
import csv
import importlib.util
import json
import math
import os
import statistics
import subprocess
import time
import warnings
from collections import defaultdict
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.io.vasp.sets import MPStaticSet

warnings.filterwarnings("ignore")

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
SCAN = BENCH / "nsim_scan"
RES = BENCH / "results"

NSIM = [4, 8, 16, 32, 64, 128]
SYSTEMS = {  # nom -> (structure, supercellule)
    "MgO_032": ("scal_MgO_032", (1, 1, 1)),
    "LiN3_144": ("aimd_LiN3_144", (1, 1, 1)),
    "Si_216": ("scal_Si_008", (3, 3, 3)),
}
CASES = [("MgO_032", "pbe"), ("LiN3_144", "pbe"), ("Si_216", "pbe"), ("MgO_032", "hse")]
FIXED = {
    "ALGO": "Fast", "NSW": 0, "IBRION": None, "ISIF": None, "ISTART": 0, "ICHARG": 2,
    "NELM": 20, "NELMIN": 20, "EDIFF": 1e-10,
    "KPAR": 1, "NCORE": 1, "NPAR": None,
    "LWAVE": False, "LCHARG": False, "LAECHG": False, "LVTOT": False, "LVHAR": False, "LELF": False,
}
# HSE : 4 pas d'initialisation sans échange exact, puis ~480 s par pas hybride (MgO_032, NSIM 4) ;
# 12 pas au lieu de 20 (8 pas hybrides) pour tenir le scan en ~13 h, identiques pour tous les NSIM.
HSE = {"LHFCALC": True, "HFSCREEN": 0.2, "AEXX": 0.25, "ALGO": "Normal", "NELM": 12, "NELMIN": 12}
SKIP_LOOPS = {"pbe": 3, "hse": 4}  # premiers pas exclus : initialisation (HSE : pas sans échange exact)
REP = "r1"  # seule exécution du scan


def load_analyze():
    spec = importlib.util.spec_from_file_location("analyze", BENCH / "scripts" / "04_analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def structure(name):
    label, sc = SYSTEMS[name]
    return Structure.from_file(BENCH / "structures" / f"{label}.json") * sc


def nbands_estimate(vis):
    """NBANDS par défaut de VASP (1 rang MPI), d'après NELECT, NIONS et les moments initiaux."""
    zval = {p.element: p.zval for p in vis.potcar}
    s = vis.structure
    nel = sum(zval[str(site.specie)] for site in s)
    nmag = sum(abs(m) for m in vis.incar.get("MAGMOM", [])) if vis.incar.get("ISPIN") == 2 else 0
    return math.ceil(max(round(nel + 2) // 2 + max(len(s) // 2, 3), int(0.6 * nel)) + nmag / 2)


def write_case(d, name, func, nsim, extra=None):
    """Écrit les entrées d'un calcul ; ne touche pas à un dossier déjà lancé."""
    if (d / "OUTCAR").exists():
        return None
    user = dict(FIXED, NSIM=nsim, **(HSE if func == "hse" else {}))
    vis = MPStaticSet(structure(name), user_potcar_functional="PBE_54", user_incar_settings=user)
    nb = nbands_estimate(vis)
    d.mkdir(parents=True, exist_ok=True)
    vis.write_input(d)
    meta = {"system": name, "func": func, "nsim": nsim, "natoms": len(vis.structure),
            "nbands_est": nb, "ngpu": 1, "kpar": 1, "omp": 8, **(extra or {})}
    (d / "nsim.json").write_text(json.dumps(meta, indent=1))
    return nb


def case_dirs(only=None, nsims=None):
    """Dossiers du scan dans l'ordre d'exécution : par cas, valeurs de NSIM croissantes."""
    out = []
    for name, func in CASES:
        if only and func != only:
            continue
        for nsim in nsims or NSIM:
            d = SCAN / name / func / f"nsim{nsim:03d}" / REP
            if (d / "INCAR").exists():
                out.append(d)
    return out


def setup(test):
    if test:
        for func in ("pbe", "hse"):
            d = SCAN / "_test" / f"MgO_032_{func}_nsim004"
            nb = write_case(d, "MgO_032", func, 4, {"rep": "test"})
            print(f"{d.relative_to(BENCH)} : NBANDS ≈ {nb}" if nb else f"{d} déjà lancé")
        return
    skipped = []
    for name, func in CASES:
        for nsim in NSIM:
            d = SCAN / name / func / f"nsim{nsim:03d}" / REP
            user = dict(FIXED, NSIM=nsim)
            nb = nbands_estimate(MPStaticSet(structure(name), user_potcar_functional="PBE_54",
                                             user_incar_settings=user))
            if nsim > nb:  # NSIM > NBANDS / nombre de rangs MPI (1 ici)
                skipped.append((name, func, nsim, nb))
                continue
            write_case(d, name, func, nsim, {"rep": REP})
    for s in sorted(set(skipped)):
        print(f"ignoré : {s[0]} {s[1]} NSIM={s[2]} > NBANDS≈{s[3]}")
    print(f"{len(case_dirs())} dossiers prêts dans {SCAN.relative_to(BENCH)}/")


def submit(test, only, after, dry, dep="afterany", nsims=None):
    if test:
        dirs = sorted((SCAN / "_test").glob("*/INCAR"))
        dirs = [p.parent for p in sorted(dirs, key=lambda p: "hse" in p.parent.name)]  # PBE d'abord
        tag = "test"
    else:
        dirs = case_dirs(only, nsims)
        tag = "scan" + (f"_{only}" if only else "")
    if not dirs:
        raise SystemExit("aucun dossier : lancer setup d'abord")
    (BENCH / "lists").mkdir(exist_ok=True)
    (BENCH / "logs").mkdir(exist_ok=True)
    lst = BENCH / "lists" / f"nsim_{tag}_{time.strftime('%Y%m%d-%H%M%S')}.txt"
    lst.write_text("\n".join(str(d) for d in dirs) + "\n")
    cmd = ["sbatch", "--parsable", f"--array=0-{len(dirs) - 1}%1", "--gres=gpu:1", "--ntasks=1",
           "--cpus-per-task=8", f"--job-name=vnsim-{tag}"] + \
          ([f"--dependency={dep}:{after}"] if after else []) + [str(BENCH / "run_nsim.slurm"), str(lst)]
    print(f"{len(dirs)} calculs, liste {lst.relative_to(BENCH)}")
    print(" ".join(cmd))
    if not dry:
        print("job", subprocess.run(cmd, cwd=BENCH, capture_output=True, text=True, check=True).stdout.strip())


def gpu_stats(path):
    """Mémoire GPU maximale (Mo) et utilisation moyenne (%) ; colonnes : date, index, mémoire, utilisation."""
    if not path.exists():
        return None, None
    mem, util = [], []
    for line in path.read_text().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 4 and p[2].isdigit() and p[3].isdigit():
            mem.append(float(p[2]))
            util.append(float(p[3]))
    return (max(mem) if mem else None), (round(statistics.mean(util), 1) if util else None)


def parse(d, an):
    meta = json.loads((d / "nsim.json").read_text())
    o = d / "OUTCAR"
    txt = o.read_text(errors="ignore") if o.exists() else ""
    loops = [float(x) for x in an.LOOP.findall(txt)]
    useful = loops[SKIP_LOOPS[meta["func"]]:]
    e = an.E0.findall(txt)
    rx = lambda k: (lambda m: int(m.group(1)) if m else None)(an.RX[k].search(txt))
    el = an.RX["elapsed_s"].findall(txt)
    loopp = an.LOOPP.findall(txt)
    mem, util = gpu_stats(d / "gpu_monitor.csv")
    t = json.loads((d / "timing.json").read_text()) if (d / "timing.json").exists() else {}
    status = "ok" if "General timing" in txt else ("non lancé" if not t else "échec")
    if status == "échec":
        so = (d / "vasp.stdout").read_text(errors="ignore")[-5000:] if (d / "vasp.stdout").exists() else ""
        if "out of memory" in so.lower() or "OUT_OF_MEMORY" in so:
            status = "échec (mémoire GPU)"
    return {
        "system": meta["system"], "func": meta["func"], "natoms": meta["natoms"],
        "NBANDS": rx("nbands") or meta["nbands_est"], "nkpts": rx("nkpts"),
        "nGPU": 1, "KPAR": 1, "NSIM": meta["nsim"], "rep": meta.get("rep", d.name),
        "n_loop": len(loops),
        "t_loop_mean": round(statistics.mean(useful), 4) if useful else None,
        "t_loop_std": round(statistics.stdev(useful), 4) if len(useful) > 1 else None,
        "t_total": float(el[-1]) if el else None,
        "t_loopplus": float(loopp[-1]) if loopp else None,
        "mem_GPU_max_MiB": mem, "util_GPU_mean": util,
        "E_tot": float(e[-1]) if e else None,
        "other_jobs_on_node": t.get("other_jobs_on_node"), "status": status, "dir": str(d.relative_to(BENCH)),
    }


def last_de(d):
    """|dE| du dernier pas électronique (OSZICAR), None si absent."""
    try:
        lines = [l for l in (d / "OSZICAR").read_text().splitlines() if l.startswith(("DAV:", "RMM:"))]
        return abs(float(lines[-1].split()[3])) if lines else None
    except (OSError, ValueError, IndexError):
        return None


def analyze(test):
    an = load_analyze()
    dirs = sorted((SCAN / "_test").glob("*/nsim.json")) if test else sorted(SCAN.glob(f"[!_]*/*/*/{REP}/nsim.json"))
    rows = [parse(p.parent, an) for p in dirs]
    if test:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return
    RES.mkdir(exist_ok=True)
    keys = list(rows[0].keys())
    with (RES / "nsim_scan.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    groups = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            groups[(r["system"], r["func"], r["NSIM"])].append(r)
    summary = {k: {"t_loop": g[0]["t_loop_mean"], "t_total": g[0]["t_total"],
                   "mem": g[0]["mem_GPU_max_MiB"] or 0, "util": g[0]["util_GPU_mean"] or 0,
                   "E": g[0]["E_tot"]} for k, g in groups.items()}
    plot(summary)
    report(summary, rows)
    print(f"{len(rows)} calculs, {sum(r['status'] == 'ok' for r in rows)} terminés")


def plot(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for what, ylabel, fname in (("t_loop", "Temps par pas électronique (s)", "nsim_time.png"),
                                ("mem", "Mémoire GPU maximale (Go)", "nsim_mem.png")):
        fig, ax = plt.subplots(figsize=(6.5, 4.2))
        for name, func in CASES:
            pts = sorted((n, v[what]) for (s, f, n), v in summary.items() if s == name and f == func)
            if not pts:
                continue
            x, y = zip(*pts)
            y = [v / 1024 for v in y] if what == "mem" else y
            ax.plot(x, y, "o-", label=f"{name} {func.upper()}, 1 GPU")
        ax.set_xscale("log", base=2)
        ax.set_xticks(NSIM, [str(n) for n in NSIM])
        if what == "t_loop":
            ax.set_yscale("log")
        ax.set_xlabel("NSIM")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(RES / fname, dpi=150)
        plt.close(fig)


def report(summary, rows):
    L = ["# Scan NSIM, VASP 6.5.1 GPU, 1 × H100", "",
         "Une exécution par valeur (r1) ; temps par pas électronique : moyenne des lignes LOOP après les "
         f"{SKIP_LOOPS['pbe']} premiers pas (HSE : {SKIP_LOOPS['hse']}, pas sans échange exact). Gain : temps NSIM = 4 / temps NSIM. ΔE : écart maximal d'énergie "
         "entre toutes les valeurs de NSIM d'un même cas.", ""]
    for name, func in CASES:
        pts = {n: v for (s, f, n), v in summary.items() if s == name and f == func}
        if not pts:
            continue
        g = [r for r in rows if r["system"] == name and r["func"] == func]
        L += [f"## {name}, {func.upper()} (NBANDS {g[0]['NBANDS']}, {g[0]['nkpts']} points k)", "",
              "| NSIM | s / pas | gain vs 4 | temps total (s) | mémoire GPU (Go) | utilisation GPU (%) |",
              "|---|---|---|---|---|---|"]
        ref = pts.get(4, {}).get("t_loop")
        for n in sorted(pts):
            v = pts[n]
            gain = f"{ref / v['t_loop']:.2f}" if ref else "–"
            L.append(f"| {n} | {v['t_loop']:.3f} | {gain} | {v['t_total']:.1f} | {v['mem'] / 1024:.1f} "
                     f"| {v['util']:.0f} |")
        es = [v["E"] for v in pts.values() if v["E"] is not None]
        best = min(pts, key=lambda n: pts[n]["t_loop"])
        dE = max(es) - min(es)
        de = [x for x in (last_de(BENCH / r["dir"]) for r in g if r["status"] == "ok") if x is not None]
        note = ""
        if dE > 1e-6:
            note = (f" (au-delà de 1E-6 eV, mais SCF non convergée en NELM pas : |dE| au dernier pas jusqu'à "
                    f"{max(de):.1e} eV ; écart dû au chemin de convergence, pas à NSIM)" if de and max(de) >= dE / 10
                    else " (au-delà de 1E-6 eV : à examiner)")
        L += ["", f"NSIM le plus rapide : {best}. ΔE max entre runs : {dE:.2e} eV/cellule{note}.", ""]
    shared = [r for r in rows if r["status"] == "ok" and r["other_jobs_on_node"]]
    if shared:
        L += ["## Conditions", "",
              f"{len(shared)} calcul(s) terminé(s) sur {sum(r['status'] == 'ok' for r in rows)} ont démarré avec "
              "d'autres jobs sur le nœud (GPU partagé ; colonne other_jobs_on_node de nsim_scan.csv). Les écarts de "
              "quelques % entre valeurs de NSIM sont du même ordre que l'effet du partage : à confirmer sur GPU seul.",
              ""]
    fails = [r for r in rows if r["status"] not in ("ok", "non lancé")]
    L += ["## Échecs", ""] + ([f"- {r['dir']} : {r['status']}" for r in fails] or ["Aucun."]) + [""]
    L += ["## Recommandation", "", "À rédiger après examen des résultats.", ""]
    (RES / "nsim_scan_report.md").write_text("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["setup", "submit", "analyze"])
    ap.add_argument("--test", action="store_true", help="essai court : MgO_032, NSIM 4, PBE et HSE")
    ap.add_argument("--only", choices=["pbe", "hse"], help="ne soumettre que ces calculs")
    ap.add_argument("--nsim", type=int, nargs="+", choices=NSIM, help="ne soumettre que ces valeurs de NSIM")
    ap.add_argument("--after", help="job Slurm à attendre")
    ap.add_argument("--dep", default="afterany", choices=["afterany", "afterok"],
                    help="afterok : ne démarrer que si le job attendu a réussi")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.action == "setup":
        setup(a.test)
    elif a.action == "submit":
        submit(a.test, a.only, a.after, a.dry_run, a.dep, a.nsim)
    else:
        analyze(a.test)


if __name__ == "__main__":
    main()
