# Benchmark VASP GPU sur vm1-ic2mp (2 × H100)

Mesure des performances de VASP 6.5.1 GPU pour des calculs en point simple (single point)
et des optimisations de géométrie, sur des structures de Materials Project de 1 à 80 atomes,
avec les deux méthodologies de MP : PBE (GGA/GGA+U) et r2SCAN.

## Mise en place (une fois)

```bash
cd ~/testing_VASP/GPU_vmp_testing_VASP_etc/benchmark
./setup.sh        # environnement Python + lien vers les POTCAR (une fois)
# Clé API : se connecter sur https://next-gen.materialsproject.org/api et copier la clé
echo "VOTRE_CLE" > ~/.mp_api_key && chmod 600 ~/.mp_api_key
source env.sh
```

`env.sh` active l'environnement Python (`.venv` : pymatgen, mp-api, matplotlib) et
indique à pymatgen les POTCAR de `/opt/vasp/POTCAR` via `psp/POT_GGA_PAW_PBE_54`.

## Chaîne de travail

| Étape | Commande | Résultat |
|---|---|---|
| 1. Structures | `python scripts/01_fetch_structures.py` | `structures/*.json`, `structures/manifest.csv` |
| 2. Entrées | `python scripts/02_make_inputs.py --variants g1` | `runs/<fonct>_<calcul>/<label>/<variante>/` |
| 3. Soumission | `python scripts/03_submit.py pbe_sp r2scan_sp --variant g1` | job array Slurm, un calcul à la fois |
| 4. Analyse | `python scripts/04_analyze.py` | `results/results.csv`, `summary.txt`, graphiques |

Toutes les étapes peuvent être relancées : les calculs terminés ne sont ni régénérés ni resoumis.
`03_submit.py --dry-run` affiche ce qui serait soumis sans rien lancer.

## Jeux de paramètres (pymatgen)

| Série | Jeu | Points clés |
|---|---|---|
| `pbe_sp` | `MPStaticSet` | ENCUT 520, GGA+U pour oxydes/fluorures de métaux de transition, 100 k/Å⁻³ |
| `pbe_relax` | `MPRelaxSet` | ISIF 3, ALGO Fast, 64 k/Å⁻³ |
| `r2scan_sp` | `MPScanStaticSet` | METAGGA R2SCAN, ENCUT 680, KSPACING 0,22 à 0,44 selon le gap MP |
| `r2scan_relax` | `MPScanRelaxSet` | ISIF 3, ALGO All, EDIFFG −0,02 |

Écarts à MP, identiques pour tous les calculs : POTCAR PBE_54 aussi pour PBE (W_pv → W_sv),
pas d'écriture des WAVECAR/CHGCAR/LOCPOT, NCORE retiré, KPAR fixé par la variante,
ISMEAR 0 au lieu de −5 si moins de 4 points k.

Variantes : `g1` (1 GPU), `g2k1` (2 GPU, KPAR=1), `g2k2` (2 GPU, KPAR=2).

## Structures

- **Séries d'échelle** (même chimie, supercellules) : Si (mp-149) 2→64 atomes,
  Al (mp-134) 1→64, MgO (mp-1265) 2→64.
- **Jeu de diversité** : pour 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72, 80 atomes,
  un matériau stable à gap > 0,3 eV et un métal, choisis de façon déterministe
  (au plus 4 éléments, sans terres rares, actinides ni W).
- Ajouts libres : `01_fetch_structures.py --ids mp-19017 mp-2657`.

## Bon usage de la machine

- `03_submit.py` limite à un calcul simultané (`--throttle 1`) : l'autre GPU reste libre.
- Ne pas soumettre en même temps une série 1 GPU et une série 2 GPU : sans limite de temps,
  Slurm ne peut pas intercaler les jobs et les deux séries s'alternent.
- Les phases à 2 GPU bloquent toute la machine : les lancer le soir ou le week-end
  et prévenir les collègues.
