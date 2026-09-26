#!/usr/bin/env python
"""Soumet une série de calculs du benchmark sous forme de job array Slurm.

Les calculs sont passés du plus petit au plus grand. Par défaut, un seul calcul
tourne à la fois (--throttle 1), ce qui laisse l'autre GPU aux collègues.
Les dossiers déjà terminés sont ignorés, on peut donc resoumettre sans risque.

Exemples :
    python scripts/03_submit.py pbe_sp --variant g1 --dry-run
    python scripts/03_submit.py pbe_sp r2scan_sp --variant g1
    python scripts/03_submit.py pbe_sp --variant g2k1 g2k2 --family scaling
    python scripts/03_submit.py pbe_relax --variant g1 --max-atoms 32
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))


def done(d):
    o = d / "OUTCAR"
    return o.exists() and "General timing" in o.read_text(errors="ignore")[-20000:]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", nargs="+", help="pbe_sp, pbe_relax, r2scan_sp, r2scan_relax")
    ap.add_argument("--variant", nargs="+", default=["g1"])
    ap.add_argument("--family", nargs="+", default=["scaling", "diversity", "user"])
    ap.add_argument("--min-atoms", type=int, default=1)
    ap.add_argument("--max-atoms", type=int, default=80)
    ap.add_argument("--throttle", type=int, default=1, help="calculs simultanés au maximum (défaut 1)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="répétitions (copies r2, r3… des dossiers) pour mesurer la dispersion des temps")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    by_ngpu = {}
    for series in args.series:
        for bj in sorted((BENCH / "runs" / series).glob("*/*/bench.json")):
            m = json.loads(bj.read_text())
            if bj.parent.name != m["variant"]:  # copie de répétition (g1_r2…) : traitée avec l'original
                continue
            if m["variant"] not in args.variant or m["family"] not in args.family:
                continue
            if not args.min_atoms <= m["nsites"] <= args.max_atoms:
                continue
            dirs = [bj.parent]
            for k in range(2, args.repeat + 1):  # répétitions : copie des entrées seulement
                rep = bj.parent.with_name(f"{bj.parent.name}_r{k}")
                if not rep.exists():
                    rep.mkdir()
                    for f in ("INCAR", "KPOINTS", "POSCAR", "POTCAR", "bench.json"):
                        if (bj.parent / f).exists():
                            (rep / f).write_bytes((bj.parent / f).read_bytes())
                dirs.append(rep)
            for d in dirs:
                if not done(d):
                    by_ngpu.setdefault(m["ngpu"], []).append((m["nsites"], str(d)))

    if not by_ngpu:
        print("Rien à soumettre (tout est terminé ou rien ne correspond aux filtres).")
        return
    (BENCH / "lists").mkdir(exist_ok=True)
    (BENCH / "logs").mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for ngpu, items in sorted(by_ngpu.items()):
        items.sort()
        lst = BENCH / "lists" / f"{'+'.join(args.series)}_{ngpu}gpu_{stamp}.txt"
        lst.write_text("\n".join(d for _, d in items) + "\n")
        cmd = ["sbatch", f"--array=0-{len(items) - 1}%{args.throttle}", f"--gres=gpu:{ngpu}",
               f"--ntasks={ngpu}", f"--job-name=vb-{'+'.join(args.series)}-{ngpu}g",
               str(BENCH / "run_bench.slurm"), str(lst)]
        print(f"{len(items)} calculs sur {ngpu} GPU ({items[0][0]} à {items[-1][0]} atomes) -> {lst.name}")
        print("  " + " ".join(cmd))
        if not args.dry_run:
            print("  " + subprocess.run(cmd, cwd=BENCH, capture_output=True, text=True, check=True).stdout.strip())


if __name__ == "__main__":
    main()
