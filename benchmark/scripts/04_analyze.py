#!/usr/bin/env python
"""Dépouille les calculs terminés : temps, taille du problème, énergies, mémoire GPU.

Sortie :
  results/results.csv       une ligne par calcul
  results/summary.txt       tableaux récapitulatifs (temps, accélération 2 GPU, coût r2SCAN/PBE)
  results/*.png             graphiques

Usage :
    python scripts/04_analyze.py
"""
import csv
import json
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
RES = BENCH / "results"

RX = {
    "nkpts": re.compile(r"NKPTS\s*=\s*(\d+)"),
    "nbands": re.compile(r"NBANDS\s*=\s*(\d+)"),
    "nplwv": re.compile(r"NPLWV\s*=\s*(\d+)"),
    "nelect": re.compile(r"NELECT\s*=\s*([\d.]+)"),
    "ispin": re.compile(r"ISPIN\s*=\s*(\d)"),
    "encut": re.compile(r"ENCUT\s*=\s*([\d.]+)"),
    "elapsed_s": re.compile(r"Elapsed time \(sec\):\s*([\d.]+)"),
    "host_mem_mb": re.compile(r"Maximum memory used \(kb\):\s*([\d.]+)"),
}
LOOP = re.compile(r"^\s*LOOP:\s+cpu time\s+[\d.]+:\s+real time\s+([\d.]+)", re.M)
LOOPP = re.compile(r"^\s*LOOP\+:\s+cpu time\s+[\d.]+:\s+real time\s+([\d.]+)", re.M)
E0 = re.compile(r"energy\(sigma->0\)\s*=\s*(-?[\d.]+)")


def parse_outcar(path):
    txt = path.read_text(errors="ignore")
    out = {}
    for k, rx in RX.items():
        m = rx.search(txt) if k not in ("elapsed_s", "host_mem_mb") else None
        if k in ("elapsed_s", "host_mem_mb"):
            ms = rx.findall(txt)
            m = ms[-1] if ms else None
            out[k] = float(m) if m else None
        else:
            out[k] = float(m.group(1)) if m else None
    if out["host_mem_mb"]:
        out["host_mem_mb"] = round(out["host_mem_mb"] / 1024, 1)
    scf = [float(x) for x in LOOP.findall(txt)]
    ion = [float(x) for x in LOOPP.findall(txt)]
    e = E0.findall(txt)
    out.update(
        finished="General timing" in txt,
        converged_relax="reached required accuracy" in txt,
        n_scf=len(scf), n_ionic=len(ion),
        t_scf_mean_s=round(statistics.mean(scf), 4) if scf else None,
        t_scf_median_s=round(statistics.median(scf), 4) if scf else None,
        t_ionic_mean_s=round(statistics.mean(ion), 2) if ion else None,
        e0_eV=float(e[-1]) if e else None,
    )
    return out


def gpu_peak(path):
    """Mémoire GPU maximale (Mo) et utilisation moyenne (%) d'après gpu_monitor.csv."""
    if not path.exists():
        return None, None
    mem, util = defaultdict(float), []
    for line in path.read_text().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 5 and p[3].replace(".", "").isdigit():
            mem[p[1]] = max(mem[p[1]], float(p[3]))
            if p[2].isdigit():
                util.append(float(p[2]))
    return (max(mem.values()) if mem else None), (round(statistics.mean(util), 1) if util else None)


def collect():
    rows = []
    for bj in sorted((BENCH / "runs").glob("*/*/*/bench.json")):
        d = bj.parent
        if not (d / "OUTCAR").exists():
            continue
        r = json.loads(bj.read_text())
        r["dir"] = str(d.relative_to(BENCH))
        r["repeat"] = d.name.split("_r")[1] if "_r" in d.name else "1"
        r.update(parse_outcar(d / "OUTCAR"))
        if (d / "timing.json").exists():
            t = json.loads((d / "timing.json").read_text())
            r.update(wall_s=t.get("wall_s"), return_code=t.get("return_code"),
                     other_jobs_on_node=t.get("other_jobs_on_node"), slurm_job=t.get("slurm_job"))
        r["gpu_mem_peak_mb"], r["gpu_util_mean_pct"] = gpu_peak(d / "gpu_monitor.csv")
        if r.get("e0_eV") is not None:
            r["e_per_atom_eV"] = round(r["e0_eV"] / r["nsites"], 5)
            ref = r.get("mp_uncorrected_energy_per_atom")
            r["dE_vs_MP_meV_atom"] = round(1000 * (r["e_per_atom_eV"] - float(ref)), 1) if ref else None
        r["kmesh"] = "x".join(map(str, r.get("kmesh", [])))
        rows.append(r)
    return rows


def summarize(rows):
    lines = []
    ok = [r for r in rows if r["finished"]]
    lines.append(f"{len(rows)} calculs trouvés, {len(ok)} terminés.\n")

    def key(r):
        return (r["func"], r["calc"], r["label"])

    # 1. Temps sur 1 GPU
    lines.append("== Temps sur 1 GPU (variante g1) ==")
    lines.append(f"{'série':14s} {'label':34s} {'at':>3s} {'NKPTS':>5s} {'NBANDS':>6s} {'SCF':>4s} "
                 f"{'ion':>3s} {'t/SCF s':>8s} {'total s':>8s} {'GPU Go':>6s} {'dE meV/at':>9s}")
    for r in sorted((r for r in ok if r["variant"] == "g1" and r["repeat"] == "1"),
                    key=lambda r: (r["func"], r["calc"], r["nsites"], r["label"])):
        gm = f"{r['gpu_mem_peak_mb'] / 1024:.1f}" if r["gpu_mem_peak_mb"] else "-"
        de = r.get("dE_vs_MP_meV_atom")
        lines.append(f"{r['func'] + '_' + r['calc']:14s} {r['label'][:34]:34s} {r['nsites']:3d} "
                     f"{int(r['nkpts'] or 0):5d} {int(r['nbands'] or 0):6d} {r['n_scf']:4d} {r['n_ionic']:3d} "
                     f"{r['t_scf_median_s'] or 0:8.2f} {r['elapsed_s'] or 0:8.1f} {gm:>6s} "
                     f"{'' if de is None else f'{de:9.1f}'}")

    # 2. Accélération 2 GPU
    idx = {(key(r), r["variant"]): r for r in ok if r["repeat"] == "1"}
    lines.append("\n== Accélération sur 2 GPU (temps g1 / temps 2 GPU, temps total) ==")
    for (k, v), r in sorted(idx.items()):
        if v == "g1":
            s = [f"{v2}: {r['elapsed_s'] / idx[(k, v2)]['elapsed_s']:.2f}x"
                 for v2 in ("g2k1", "g2k2") if (k, v2) in idx and idx[(k, v2)]["elapsed_s"]]
            if s:
                lines.append(f"{k[0] + '_' + k[1]:14s} {k[2][:34]:34s} {r['nsites']:3d} at  " + "  ".join(s))

    # 3. Coût r2SCAN / PBE
    lines.append("\n== Coût r2SCAN / PBE sur 1 GPU (temps total, et par pas SCF) ==")
    for (k, v), r in sorted(idx.items()):
        if v == "g1" and k[0] == "r2scan":
            p = idx.get((("pbe", k[1], k[2]), "g1"))
            if p and p["elapsed_s"] and p["t_scf_median_s"]:
                lines.append(f"{k[1]:6s} {k[2][:34]:34s} {r['nsites']:3d} at  total {r['elapsed_s'] / p['elapsed_s']:5.1f}x"
                             f"   par pas SCF {r['t_scf_median_s'] / p['t_scf_median_s']:5.1f}x")

    # 4. Reproductibilité
    reps = defaultdict(list)
    for r in ok:
        reps[(key(r), r["variant"])].append(r["elapsed_s"])
    rep_lines = [f"{k[0][0]}_{k[0][1]} {k[0][2][:34]:34s} {k[1]:5s} n={len(v)}  moyenne {statistics.mean(v):.1f} s  "
                 f"écart relatif {100 * statistics.stdev(v) / statistics.mean(v):.1f} %"
                 for k, v in sorted(reps.items()) if len(v) > 1]
    if rep_lines:
        lines.append("\n== Reproductibilité (répétitions) ==")
        lines += rep_lines

    bad = [r for r in rows if not r["finished"] or r.get("return_code") not in (0, None)]
    if bad:
        lines.append("\n== Calculs interrompus ou en erreur ==")
        lines += [f"{r['dir']}  (code {r.get('return_code')})" for r in bad]
    return "\n".join(lines)


def plots(rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    ink, muted, grid = "#1f2328", "#5b6068", "#e3e5e8"
    col = {"pbe": "#2a78d6", "r2scan": "#eb6834"}          # fonctionnelle : couleur
    mark = {"pbe": "o", "r2scan": "s"}                       # et forme (lisible sans couleur)
    name = {"pbe": "PBE (MP)", "r2scan": "r2SCAN (MP)"}
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink})
    g1 = [r for r in rows if r["finished"] and r["variant"] == "g1" and r["repeat"] == "1"]

    for metric, ylabel, fname in (("t_scf_median_s", "Temps par pas SCF (s, médiane)", "t_scf_vs_natoms"),
                                  ("elapsed_s", "Temps total (s)", "t_total_vs_natoms")):
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
        for ax, calc, title in zip(axes, ("sp", "relax"), ("Point simple", "Optimisation")):
            for func in ("pbe", "r2scan"):
                pts = sorted((r["nsites"], r[metric]) for r in g1
                             if r["func"] == func and r["calc"] == calc and r[metric])
                if pts:
                    ax.scatter(*zip(*pts), s=36, marker=mark[func], color=col[func], label=name[func],
                               edgecolors="white", linewidths=1, zorder=3)
            ax.set_xscale("log", base=2)
            ax.set_yscale("log")
            ax.set_title(title, loc="left", fontsize=11, color=ink)
            ax.set_xlabel("Atomes dans la maille")
            ax.grid(True, which="major", color=grid, linewidth=0.8)
            ax.spines[["top", "right"]].set_visible(False)
        axes[0].set_ylabel(ylabel)
        axes[0].legend(frameon=False, loc="upper left")
        fig.suptitle(f"{ylabel} sur 1 GPU H100", x=0.01, ha="left", fontsize=12)
        fig.tight_layout()
        fig.savefig(RES / f"{fname}.png", dpi=150)
        plt.close(fig)

    # Accélération 2 GPU
    idx = {(r["func"], r["calc"], r["label"], r["variant"]): r for r in rows
           if r["finished"] and r["repeat"] == "1"}
    fig, ax = plt.subplots(figsize=(6, 4))
    for v, c, m, lab in (("g2k1", "#2a78d6", "o", "2 GPU, KPAR=1"), ("g2k2", "#1baf7a", "^", "2 GPU, KPAR=2")):
        pts = [(r["nsites"], r["elapsed_s"] / idx[(f, cl, lb, v)]["elapsed_s"])
               for (f, cl, lb, vv), r in idx.items()
               if vv == "g1" and (f, cl, lb, v) in idx and idx[(f, cl, lb, v)]["elapsed_s"]]
        if pts:
            ax.scatter(*zip(*sorted(pts)), s=36, marker=m, color=c, label=lab,
                       edgecolors="white", linewidths=1, zorder=3)
    ax.axhline(1, color=muted, linewidth=1, linestyle="--")
    ax.axhline(2, color=grid, linewidth=1)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Atomes dans la maille")
    ax.set_ylabel("Accélération (temps 1 GPU / temps 2 GPU)")
    ax.grid(True, color=grid, linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(RES / "speedup_2gpu.png", dpi=150)
    plt.close(fig)


def main():
    RES.mkdir(exist_ok=True)
    rows = collect()
    if not rows:
        print("Aucun OUTCAR trouvé dans runs/.")
        return
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (RES / "results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    text = summarize(rows)
    (RES / "summary.txt").write_text(text + "\n")
    plots(rows)
    print(text)
    print(f"\nFichiers écrits dans {RES}")


if __name__ == "__main__":
    main()
