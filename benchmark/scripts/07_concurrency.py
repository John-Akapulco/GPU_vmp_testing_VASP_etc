#!/usr/bin/env python
"""Plusieurs calculs VASP en même temps sur une seule GPU, avec ou sans MPS.

Repart des entrées g1 déjà calculées (runs/<série>/<label>/g1 : INCAR, KPOINTS, POSCAR, POTCAR,
copiés sans modification) et lance N copies identiques dans un seul job Slurm à 1 GPU :
  nomps  les N processus se partagent la GPU par tranches de temps (comportement par défaut) ;
  mps    un démon NVIDIA MPS propre au job laisse les noyaux des N processus s'exécuter ensemble.
Slurm ne partage pas les GPU entre jobs sur vm1-ic2mp (gres gpu seul) : tout se passe dans un job.

Référence : le temps du calcul seul en g1 (moyenne des répétitions g1, g1_r2, g1_r3 s'il y en a).
Gain de débit = N × t_ref / T_lot, où T_lot va du premier démarrage à la dernière fin.

Arborescence : runs_conc/<série>/<label>/n<N>_<mode>/copy-<i>/

Usage :
    python scripts/07_concurrency.py                        # prépare les dossiers
    python scripts/07_concurrency.py --submit --dry-run     # affiche la commande sbatch
    python scripts/07_concurrency.py --submit --after 173   # soumet (à ne faire qu'après accord)
    python scripts/07_concurrency.py --analyze              # results/concurrency.csv, _summary.txt, .png
"""
import argparse
import csv
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
RUNS, CONC, RES = BENCH / "runs", BENCH / "runs_conc", BENCH / "results"

# (série, label) -> nombres de copies simultanées. Les trois premiers ont une référence répétée
# 3 fois (écart ≤ 1 %) ; Si 64 at. r2SCAN est limité à 4 copies par la mémoire hôte (~4,2 Go chacune,
# ~28 Go par job).
CASES = {
    ("pbe_sp", "scal_Si_008"): [1, 2, 4, 8],
    ("pbe_sp", "scal_MgO_032"): [1, 2, 4, 8],
    ("r2scan_sp", "scal_Al_032"): [1, 2, 4, 8],
    ("r2scan_sp", "scal_Si_064"): [1, 2, 4],
}
MODES = ["nomps", "mps"]
INPUTS = ["INCAR", "KPOINTS", "POSCAR", "POTCAR"]  # KPOINTS absent en r2SCAN (KSPACING)
MAX_COPIES = 8  # cœurs demandés à Slurm : un rang MPI par copie (16 cœurs par GPU)


def load_analyze():
    spec = importlib.util.spec_from_file_location("analyze", BENCH / "scripts" / "04_analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def batches():
    """Lots à calculer, du plus court au plus long ; N=1 sans MPS n'est pas relancé (c'est la référence)."""
    out = []
    for (series, label), ns in CASES.items():
        for n in ns:
            for mode in MODES:
                if n == 1 and mode == "nomps":
                    continue
                out.append((series, label, n, mode))
    ref = {k: t_ref(*k) or 0 for k in CASES}
    return sorted(out, key=lambda b: (ref[(b[0], b[1])] * b[2], b[3]))


def t_ref(series, label):
    ana = load_analyze()
    ts = []
    for d in sorted((RUNS / series / label).glob("g1*")):
        if (d / "OUTCAR").exists():
            p = ana.parse_outcar(d / "OUTCAR")
            if p["finished"] and p["elapsed_s"]:
                ts.append(p["elapsed_s"])
    return statistics.mean(ts) if ts else None


def setup():
    for series, label, n, mode in batches():
        src = RUNS / series / label / "g1"
        d = CONC / series / label / f"n{n}_{mode}"
        for i in range(1, n + 1):
            c = d / f"copy-{i}"
            c.mkdir(parents=True, exist_ok=True)
            for f in INPUTS:
                if (src / f).exists() and not (c / f).exists():
                    shutil.copy(src / f, c / f)
        meta = json.loads((src / "bench.json").read_text())
        meta.update(variant=f"n{n}_{mode}", ncopies=n, mode=mode, source=str(src.relative_to(BENCH)))
        (d / "conc.json").write_text(json.dumps(meta, indent=1))
    todo = batches()
    print(f"{len(todo)} lots, {sum(b[2] for b in todo)} calculs VASP")
    est = 0
    for series, label, n, mode in todo:
        t = t_ref(series, label)
        est += n * t
        print(f"  {series:<10} {label:<14} N={n} {mode:<5}  pire cas {n * t / 60:6.1f} min")
    print(f"Durée au pire (aucun gain) : {est / 3600:.1f} h sur 1 GPU")


def submit(after, dry):
    dirs = [str(CONC / s / l / f"n{n}_{m}") for s, l, n, m in batches()]
    (BENCH / "lists").mkdir(exist_ok=True)
    (BENCH / "logs").mkdir(exist_ok=True)
    lst = BENCH / "lists" / f"concurrency_{time.strftime('%Y%m%d-%H%M%S')}.txt"
    lst.write_text("\n".join(dirs) + "\n")
    cmd = ["sbatch", f"--array=0-{len(dirs) - 1}%1", "--gres=gpu:1", f"--ntasks={MAX_COPIES}",
           "--job-name=vconc"] + ([f"--dependency=afterany:{after}"] if after else []) + \
          [str(BENCH / "run_conc.slurm"), str(lst)]
    print(" ".join(cmd))
    if not dry:
        print(subprocess.run(cmd, cwd=BENCH, capture_output=True, text=True, check=True).stdout.strip())


def analyze():
    ana = load_analyze()
    rows = []
    for (series, label), ns in CASES.items():
        tr = t_ref(series, label)
        ref = ana.parse_outcar(RUNS / series / label / "g1" / "OUTCAR")
        for n in ns:
            for mode in MODES:
                d = CONC / series / label / f"n{n}_{mode}"
                bt = d / "timing.json"
                if n == 1 and mode == "nomps" or not bt.exists():
                    continue
                b = json.loads(bt.read_text())
                cps = [ana.parse_outcar(c / "OUTCAR") for c in sorted(d.glob("copy-*")) if (c / "OUTCAR").exists()]
                ok = [p for p in cps if p["finished"]]
                mem, util = ana.gpu_peak(d / "gpu_monitor.csv")
                de = max(abs(p["e0_eV"] - ref["e0_eV"]) for p in ok) if ok and ref["e0_eV"] else None
                rows.append({
                    "series": series, "label": label, "ncopies": n, "mode": mode,
                    "n_finished": len(ok), "t_ref_s": round(tr, 2), "t_batch_s": b["wall_s"],
                    "t_copy_mean_s": round(statistics.mean(p["elapsed_s"] for p in ok), 2) if ok else None,
                    "t_scf_median_s": round(statistics.mean(p["t_scf_median_s"] for p in ok), 4) if ok else None,
                    "t_scf_ref_s": ref["t_scf_median_s"],
                    "throughput_gain": round(n * tr / b["wall_s"], 2) if len(ok) == n else None,
                    "slowdown_per_calc": round(statistics.mean(p["elapsed_s"] for p in ok) / tr, 2) if ok else None,
                    "gpu_mem_peak_mb": mem, "gpu_util_mean_pct": util,
                    "max_dE0_vs_ref_eV": de, "other_jobs_on_node": b.get("other_jobs_on_node"),
                })
    if not rows:
        print("Aucun lot terminé.")
        return
    RES.mkdir(exist_ok=True)
    with (RES / "concurrency.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    lines = [f"{'série':<10} {'label':<14} {'N':>2} {'mode':<5} {'t_ref s':>8} {'T_lot s':>8} "
             f"{'gain débit':>10} {'ralent.':>7} {'GPU Go':>6} {'util %':>6} {'|ΔE| eV':>8}"]
    for r in rows:
        lines.append(f"{r['series']:<10} {r['label']:<14} {r['ncopies']:>2} {r['mode']:<5} {r['t_ref_s']:8.1f} "
                     f"{r['t_batch_s']:8.1f} {r['throughput_gain'] or 0:9.2f}x {r['slowdown_per_calc'] or 0:6.2f}x "
                     f"{(r['gpu_mem_peak_mb'] or 0) / 1024:6.1f} {r['gpu_util_mean_pct'] or 0:6.1f} "
                     f"{r['max_dE0_vs_ref_eV'] if r['max_dE0_vs_ref_eV'] is not None else float('nan'):8.1e}")
    (RES / "concurrency_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    plot(rows)


def plot(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    for (series, label) in CASES:
        for mode, ls in (("nomps", "--"), ("mps", "-")):
            pts = [(1, 1.0)] + [(r["ncopies"], r["throughput_gain"]) for r in rows
                                if (r["series"], r["label"], r["mode"]) == (series, label, mode)
                                and r["throughput_gain"] and r["ncopies"] > 1]
            if len(pts) > 1:
                ax.plot(*zip(*sorted(pts)), ls, marker="o", label=f"{series} {label.split('_', 1)[1]} {mode}")
    ax.plot([1, MAX_COPIES], [1, MAX_COPIES], ":", color="grey", lw=1, label="idéal")
    ax.set_xlabel("Calculs simultanés sur une GPU")
    ax.set_ylabel("Gain de débit (N × t_ref / T_lot)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(RES / "concurrency_throughput.png", dpi=150)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--after", help="job Slurm dont la fin conditionne le démarrage (afterany)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args()
    if args.analyze:
        analyze()
    elif args.submit:
        submit(args.after, args.dry_run)
    else:
        setup()


if __name__ == "__main__":
    main()
