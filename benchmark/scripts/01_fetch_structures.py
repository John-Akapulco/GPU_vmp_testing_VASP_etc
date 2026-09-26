#!/usr/bin/env python
"""Télécharge les structures du benchmark depuis Materials Project.

Deux familles de structures :
  - séries d'échelle : un même matériau en supercellules de taille croissante
    (Si semi-conducteur, Al métal, MgO isolant), pour mesurer le coût en
    fonction du nombre d'atomes à chimie constante ;
  - jeu de diversité : des matériaux stables de MP choisis automatiquement
    par taille de maille (1 à 80 atomes), un métal et un non-métal par taille.

Sortie : structures/<label>.json (Structure pymatgen) et structures/manifest.csv

Usage :
    source env.sh
    python scripts/01_fetch_structures.py              # sélection par défaut
    python scripts/01_fetch_structures.py --ids mp-19017 mp-2657   # ajouts manuels
"""
import argparse
import csv
import os
import sys
from pathlib import Path

from mp_api.client import MPRester
from pymatgen.core import Element
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

BENCH = Path(os.environ.get("BENCH_DIR", Path(__file__).resolve().parents[1]))
OUT = BENCH / "structures"

# Séries d'échelle : (mp-id, formule attendue, supercellules appliquées à la maille conventionnelle)
# nombre d'atomes obtenu : Si 2 (primitive), 8, 16, 32, 64 ; Al 1, 4, 32, 64 ; MgO 2, 8, 16, 32, 64
SCALING = {
    "mp-149": ("Si", [None, (1, 1, 1), (2, 1, 1), (2, 2, 1), (2, 2, 2)]),
    "mp-134": ("Al", [None, (1, 1, 1), (2, 2, 2), (2, 2, 4)]),
    "mp-1265": ("MgO", [None, (1, 1, 1), (2, 1, 1), (2, 2, 1), (2, 2, 2)]),
}

# Tailles visées pour le jeu de diversité
TARGET_SIZES = [1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72, 80]

# Éléments exclus : terres rares et actinides (traitement MP particulier), gaz rares,
# radioactifs, et W (POTCAR W_pv du jeu PBE de MP absent de /opt/vasp/POTCAR).
EXCLUDED = {el.symbol for el in Element if el.is_rare_earth or el.is_actinoid or el.is_noble_gas
            or el.Z > 83} | {"W", "Tc", "Po", "At"}

FIELDS = ["material_id", "formula_pretty", "nsites", "nelements", "elements", "band_gap",
          "is_metal", "is_magnetic", "total_magnetization", "energy_above_hull",
          "symmetry", "uncorrected_energy_per_atom", "energy_per_atom", "structure"]


def row(label, doc, structure, family, note=""):
    return {
        "label": label,
        "family": family,
        "material_id": str(doc.material_id),
        "formula": structure.composition.reduced_formula,
        "nsites": len(structure),
        "spacegroup": doc.symmetry.symbol if doc.symmetry else "",
        "band_gap_eV": round(doc.band_gap or 0.0, 4),
        "is_metal": bool(doc.is_metal),
        "is_magnetic": bool(doc.is_magnetic),
        "mp_uncorrected_energy_per_atom": doc.uncorrected_energy_per_atom,
        "mp_E_pbe": "",
        "mp_E_r2scan": "",
        "note": note,
    }


def pick_diversity(mpr, n):
    """Choisit pour n atomes un non-métal puis un métal, stables, de façon déterministe."""
    docs = mpr.materials.summary.search(num_sites=(n, n), is_stable=True,
                                        num_elements=(1, 4), fields=FIELDS)
    docs = [d for d in docs if not ({str(e) for e in d.elements} & EXCLUDED)]
    docs.sort(key=lambda d: int(str(d.material_id).split("-")[1]))
    picks = []
    for want_metal in (False, True):
        cand = [d for d in docs if bool(d.is_metal) == want_metal]
        if want_metal is False:  # un gap franc, pour éviter les cas limites
            cand = [d for d in cand if (d.band_gap or 0) > 0.3]
        if cand:
            picks.append(cand[0])
    return picks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", nargs="*", default=[], help="mp-id supplémentaires à ajouter tels quels")
    ap.add_argument("--no-scaling", action="store_true", help="ne pas générer les séries d'échelle")
    ap.add_argument("--no-diversity", action="store_true", help="ne pas générer le jeu de diversité")
    ap.add_argument("--max-atoms", type=int, default=80)
    args = ap.parse_args()

    if not os.environ.get("MP_API_KEY"):
        sys.exit("MP_API_KEY absente : créez ~/.mp_api_key (voir env.sh) puis relancez `source env.sh`.")

    OUT.mkdir(exist_ok=True)
    rows = []
    with MPRester() as mpr:
        if not args.no_scaling:
            for mpid, (formula, cells) in SCALING.items():
                doc = mpr.materials.summary.search(material_ids=[mpid], fields=FIELDS)[0]
                prim = doc.structure
                if prim.composition.reduced_formula != formula:
                    sys.exit(f"{mpid} : formule {prim.composition.reduced_formula}, attendu {formula}")
                conv = SpacegroupAnalyzer(prim).get_conventional_standard_structure()
                for sc in cells:
                    s = prim.copy() if sc is None else conv * sc
                    if len(s) > args.max_atoms:
                        continue
                    label = f"scal_{formula}_{len(s):03d}"
                    s.to(filename=str(OUT / f"{label}.json"))
                    note = "maille primitive" if sc is None else f"conventionnelle x {sc}"
                    rows.append(row(label, doc, s, "scaling", note))
                    print(f"{label:24s} {mpid:10s} {len(s):3d} atomes  {note}")

        if not args.no_diversity:
            for n in [t for t in TARGET_SIZES if t <= args.max_atoms]:
                for doc in pick_diversity(mpr, n):
                    kind = "metal" if doc.is_metal else "gap"
                    label = f"div_{n:03d}_{kind}_{doc.formula_pretty}"
                    doc.structure.to(filename=str(OUT / f"{label}.json"))
                    rows.append(row(label, doc, doc.structure, "diversity"))
                    print(f"{label:34s} {doc.material_id!s:12s} gap={doc.band_gap:.2f} eV  "
                          f"mag={bool(doc.is_magnetic)}")

        if args.ids:
            for doc in mpr.materials.summary.search(material_ids=args.ids, fields=FIELDS):
                label = f"user_{len(doc.structure):03d}_{doc.formula_pretty}_{doc.material_id}"
                doc.structure.to(filename=str(OUT / f"{label}.json"))
                rows.append(row(label, doc, doc.structure, "user"))
                print(f"{label:34s} ajouté")

        # Énergies de référence MP par fonctionnelle (non corrigées, eV/atome), pour vérification
        ids = sorted({r["material_id"] for r in rows})
        for ttype, key in (("GGA_GGA+U", "mp_E_pbe"), ("R2SCAN", "mp_E_r2scan")):
            try:
                docs = mpr.materials.thermo.search(material_ids=ids, thermo_types=[ttype],
                                                   fields=["material_id", "uncorrected_energy_per_atom"])
            except Exception as exc:
                print(f"thermo {ttype} indisponible : {exc}")
                continue
            e = {str(d.material_id): d.uncorrected_energy_per_atom for d in docs}
            for r in rows:
                r[key] = e.get(r["material_id"], "")

    manifest = OUT / "manifest.csv"
    old = []
    if manifest.exists():
        with manifest.open() as f:
            old = [r for r in csv.DictReader(f) if r["label"] not in {r2["label"] for r2 in rows}]
    with manifest.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(old + rows)
    print(f"\n{len(rows)} structures écrites, manifeste : {manifest}")


if __name__ == "__main__":
    main()
