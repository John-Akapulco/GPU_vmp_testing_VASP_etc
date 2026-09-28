#!/usr/bin/env python
"""Bilan des campagnes de phonons : temps de calcul, temps de restitution, qualité des phonons.

Deux mesures du coût :
  - temps de calcul : somme des temps VASP de la chaîne, et GPU·h = nGPU × cette somme ;
  - temps de restitution (« temps humain ») : de la soumission au résultat final
    (attente dans la file + calculs + post-traitement), par matériau et par campagne.
Qualité : convergence en taille de supercellule (écart des fréquences aux points spéciaux,
Cv et S à 300 K), absence de fréquences imaginaires, écart entre variantes 1 GPU / 2 GPU.

Sorties : results/phonons_steps.csv, results/phonons_summary.txt
"""
import csv
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
PH = BENCH / "phonons"


def t(s):
    # l'heure de soumission est écrite sans fuseau : la prendre comme heure locale
    return datetime.fromisoformat(s).astimezone() if s else None


def secs(a, b):
    return (b - a).total_seconds() if a and b else float("nan")


THZ_TO_CM = 33.35641  # 1 THz en cm⁻¹


def band_segments(path):
    """Branches de band.yaml par segment du chemin : [(x, fréquences cm⁻¹ (nq, nbandes))], étiquettes."""
    import yaml
    b = yaml.safe_load(path.read_text())
    x = np.array([p["distance"] for p in b["phonon"]])
    f = np.array([[m["frequency"] for m in p["band"]] for p in b["phonon"]]) * THZ_TO_CM
    segs, i = [], 0
    for n in b["segment_nqpoint"]:
        segs.append((x[i:i + n], f[i:i + n]))
        i += n
    return segs, b["labels"]


def plot_dispersions(chains):
    """Dispersions en cm⁻¹, variante g1 : plus grande supercellule en trait plein, plus petite en tirets."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    ink, muted, grid, blue = "#1f2328", "#5b6068", "#e3e5e8", "#2a78d6"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink})
    order = {"Si": 0, "Al": 1, "MgO": 2}
    labels = sorted((l for l, v in chains if v == "g1"), key=lambda l: (order.get(chains[(l, "g1")][0]["formula"], 9), l))
    if not labels:
        return
    fig, axes = plt.subplots(1, len(labels), figsize=(4 * len(labels), 3.8))
    for ax, label in zip(np.atleast_1d(axes), labels):
        meta = chains[(label, "g1")][0]
        d = PH / label / "g1"
        ns = sorted(meta["supercells"])
        for n, style, name in ((ns[0], dict(color=muted, linewidth=1.0, linestyle=(0, (3, 2))), "Smaller supercell"),
                               (ns[-1], dict(color=blue, linewidth=1.4), "Larger supercell")):
            f = d / f"sc{n}" / "band.yaml"
            if not f.exists():
                continue
            segs, labs = band_segments(f)
            for k, (x, fr) in enumerate(segs):
                ax.plot(x, fr, **style)
            ax.plot([], [], **style, label=f"{name}, {n}×{n}×{n} ({n ** 3 * meta['nsites_prim']} atoms)")
        # points spéciaux ; deux étiquettes à une même abscisse (saut du chemin) : « U|K »
        ticks, names = [], []
        for (x, _), (a, b) in zip(segs, labs):
            for xx, lab in ((x[0], a), (x[-1], b)):
                lab = lab.replace("$\\mathrm{", "").replace("}$", "").replace("$\\Gamma$", "Γ")
                if ticks and abs(xx - ticks[-1]) < 1e-6:
                    if lab != names[-1]:
                        names[-1] += "|" + lab
                else:
                    ticks.append(xx)
                    names.append(lab)
        ax.set_xticks(ticks, names)
        for xx in ticks[1:-1]:
            ax.axvline(xx, color=grid, linewidth=0.8, zorder=0)
        ax.axhline(0, color=muted, linewidth=0.8)
        ax.set_xlim(ticks[0], ticks[-1])
        ax.set_ylim(bottom=min(0, ax.get_ylim()[0]))
        ax.set_title(f"{meta['formula']} ({meta['material_id']})", loc="left", fontsize=11, color=ink)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="x", length=0)
    np.atleast_1d(axes)[0].set_ylabel("Frequency (cm$^{-1}$)")
    for ax in np.atleast_1d(axes):
        ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=1, fontsize=8)
    fig.tight_layout()
    fig.savefig(BENCH / "results" / "phonon_dispersions.png", dpi=200)
    plt.close(fig)


def main():
    chains = {}
    for pj in sorted(PH.glob("*/*/pipeline.json")):
        st = json.loads(pj.read_text())
        meta = json.loads((pj.parent / "phonon.json").read_text())
        chains[(meta["label"], meta["variant"])] = (meta, st)
    if not chains:
        print("Aucune chaîne de phonons trouvée.")
        return

    rows, lines = [], []
    for (label, var), (meta, st) in chains.items():
        runs = [r for r in st["runs"] if r.get("status") == "ok"] or st["runs"]
        for r in st["runs"]:
            for s in r["steps"]:
                rows.append({"label": label, "variant": var, "ngpu": meta["ngpu"], "step": s["step"],
                             "wall_s": s["wall_s"], "return_code": s.get("return_code", ""),
                             "slurm_job": r.get("slurm_job", ""), "start": s.get("start", "")})
    (BENCH / "results").mkdir(exist_ok=True)
    with (BENCH / "results" / "phonons_steps.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def vasp_time(label, var, prefix=""):
        return sum(r["wall_s"] for r in rows if r["label"] == label and r["variant"] == var
                   and r["return_code"] != "" and r["step"].startswith(prefix))

    lines.append("== Temps par chaîne (s) ==")
    lines.append(f"{'matériau':<14}{'var.':<6}{'relax':>8}{'born':>8}" + "".join(
        f"{'sc' + str(n):>9}" for n in (3, 4, 5)) + f"{'VASP':>9}{'GPU·h':>8}{'attente':>9}{'restit.':>9}  statut")
    campaigns = {}
    for (label, var), (meta, st) in sorted(chains.items()):
        first, last = st["runs"][0], st["runs"][-1]
        sub, start, end = t(first.get("submit")), t(first["start"]), t(last.get("end"))
        campaigns.setdefault(var, []).append((sub or start, end))
        tv = vasp_time(label, var)
        cells = "".join(f"{vasp_time(label, var, f'sc{n}/') if n in meta['supercells'] else float('nan'):9.0f}"
                        for n in (3, 4, 5))
        lines.append(f"{label:<14}{var:<6}{vasp_time(label, var, 'relax'):8.0f}{vasp_time(label, var, 'born'):8.0f}"
                     f"{cells}{tv:9.0f}{meta['ngpu'] * tv / 3600:8.3f}{secs(sub, start):9.0f}"
                     f"{secs(sub or start, end):9.0f}  {last.get('status')}")

    lines.append("\n== Temps de restitution par campagne (soumission du 1er calcul -> fin du dernier) ==")
    for var, ab in sorted(campaigns.items()):
        a = min(x for x, _ in ab if x)
        b = max((y for _, y in ab if y), default=None)
        lines.append(f"{var:<6} {secs(a, b) / 3600:6.2f} h  ({len(ab)} matériaux)")

    lines.append("\n== Accélération 2 GPU (temps VASP g1 / g2k1, par étape) ==")
    for label in sorted({l for l, _ in chains}):
        if (label, "g1") in chains and (label, "g2k1") in chains:
            parts = []
            for p in ["relax", "born"] + [f"sc{n}/" for n in chains[(label, "g1")][0]["supercells"]]:
                a, b = vasp_time(label, "g1", p), vasp_time(label, "g2k1", p)
                if a and b:
                    parts.append(f"{p.rstrip('/')} {a / b:4.2f}x")
            lines.append(f"{label:<14} " + "  ".join(parts) + f"  | total {vasp_time(label, 'g1') / max(vasp_time(label, 'g2k1'), 1e-9):4.2f}x")

    lines.append("\n== Qualité des phonons ==")
    for (label, var), (meta, st) in sorted(chains.items()):
        sc = st.get("supercells") or {}
        if not sc:
            continue
        ns = sorted(sc, key=int)
        big = sc[ns[-1]]
        msg = (f"{label:<14}{var:<6} fmin(maillage, hors Γ) {big['fmin_mesh_THz']:7.3f} THz  "
               f"fmax {big['fmax_mesh_THz']:6.2f}  Cv300 {big['Cv_300K_J_K_mol']:6.2f}  dérive F {big['max_force_drift_eV_A']:.1e}")
        if len(ns) > 1:
            small = sc[ns[0]]
            d = max(np.abs(np.array(big["special_points_THz"][k]) - np.array(small["special_points_THz"][k])).max()
                    for k in big["special_points_THz"] if k in small["special_points_THz"] and k != "Γ")
            msg += (f"  | sc{ns[0]}→sc{ns[-1]} : max Δf {d:.3f} THz, "
                    f"ΔCv {big['Cv_300K_J_K_mol'] - small['Cv_300K_J_K_mol']:+.3f}, "
                    f"ΔS {big['S_300K_J_K_mol'] - small['S_300K_J_K_mol']:+.3f} J/K/mol")
        lines.append(msg)
        if big["fmin_mesh_THz"] < -0.05:
            lines.append("    ATTENTION : fréquences imaginaires hors Γ")

    lines.append("\n== Reproductibilité 1 GPU / 2 GPU (plus grande supercellule) ==")
    for label in sorted({l for l, _ in chains}):
        a, b = chains.get((label, "g1")), chains.get((label, "g2k1"))
        if a and b and a[1].get("supercells") and b[1].get("supercells"):
            n = max(a[1]["supercells"], key=int)
            pa, pb = a[1]["supercells"][n]["special_points_THz"], b[1]["supercells"].get(n, {}).get("special_points_THz", {})
            d = max((np.abs(np.array(pa[k]) - np.array(pb[k])).max() for k in pa if k in pb), default=float("nan"))
            lines.append(f"{label:<14} sc{n} : max |Δf| = {d:.4f} THz")

    text = "\n".join(lines)
    (BENCH / "results" / "phonons_summary.txt").write_text(text + "\n")
    print(text)
    plot_dispersions(chains)


if __name__ == "__main__":
    main()
