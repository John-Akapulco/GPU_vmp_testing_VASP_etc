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
| 2. Entrées | `python scripts/02_make_inputs.py --variants g1 --family scaling` | `runs/<fonct>_<calcul>/<label>/<variante>/` |
| 3. Soumission | `python scripts/03_submit.py pbe_sp r2scan_sp --variant g1 --family scaling` | job array Slurm, un calcul à la fois |
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
ISMEAR 0 au lieu de −5 si moins de 4 points k irréductibles.

Variantes : `g1` (1 GPU), `g2k1` (2 GPU, KPAR=1), `g2k2` (2 GPU, KPAR=2).

## Structures

- **Séries d'échelle** (même chimie, supercellules) : Si (mp-149) 2→64 atomes,
  Al (mp-134) 1→64, MgO (mp-1265) 2→64.
- **Jeu de diversité** (reporté : téléchargé mais non calculé pour l'instant, d'où `--family scaling`) : pour 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72, 80 atomes,
  un matériau stable à gap > 0,3 eV et un métal, choisis de façon déterministe
  (au plus 4 éléments, sans terres rares, actinides ni W).
- Ajouts libres : `01_fetch_structures.py --ids mp-19017 mp-2657`.

## Bon usage de la machine

- `03_submit.py` limite à un calcul simultané (`--throttle 1`) : l'autre GPU reste libre.
- Ne pas soumettre en même temps une série 1 GPU et une série 2 GPU : sans limite de temps,
  Slurm ne peut pas intercaler les jobs et les deux séries s'alternent.
- Les phases à 2 GPU bloquent toute la machine : les lancer le soir ou le week-end
  et prévenir les collègues.

## Phonons PBE (différences finies, phonopy)

| Étape | Commande | Résultat |
|---|---|---|
| 5. Préparation | `python scripts/05_phonon_setup.py` | `phonons/<label>/<variante>/relax1/`, `phonon.json` |
| 6. Soumission | `python scripts/05_phonon_setup.py --submit g1 g2k1 [--after JOB]` | un job array par variante, la 2ᵉ attend la 1ʳᵉ |
| 7. Analyse | `python scripts/06_phonon_analyze.py` | `results/phonons_summary.txt`, `phonons_steps.csv` |

Une tâche Slurm = une chaîne complète pour un matériau (`scripts/phonon_pipeline.py`) :
relaxation serrée de la maille primitive (passes successives jusqu'à ≤ 2 pas ioniques),
charges de Born et ε∞ si le matériau est polaire (LEPSILON, repli LCALCEPS), puis pour
deux tailles de supercellule : déplacements, forces, constantes de force, bandes, DOS,
grandeurs thermodynamiques. Mêmes entrées en 1 et 2 GPU (KPAR = 1) ; seul le matériel change.
Coût mesuré : temps VASP et GPU·h ; temps de restitution (soumission → résultat, file comprise).

phonopy 4.6 est utilisé avec son moteur C : le moteur Rust installé (phonors 0.5) est incompatible.

## Calculs simultanés sur une GPU (avec et sans MPS)

Les petits systèmes n'occupent qu'une partie d'une H100 (utilisation de 10 à 50 %, 3 à 12 Go).
Ce test mesure ce que rapporte le lancement de N calculs identiques en même temps sur une seule GPU.

| Étape | Commande | Résultat |
|---|---|---|
| 8. Préparation | `python scripts/07_concurrency.py` | `runs_conc/<série>/<label>/n<N>_<mode>/copy-<i>/` |
| 9. Soumission | `python scripts/07_concurrency.py --submit [--after JOB]` | un job array à 1 GPU, un lot à la fois |
| 10. Analyse | `python scripts/07_concurrency.py --analyze` | `results/concurrency.csv`, `concurrency_summary.txt`, `concurrency_throughput.png` |

- Les copies reprennent sans modification les entrées g1 de `runs/` ; la référence est le temps g1
  déjà mesuré (moyenne des répétitions quand il y en a).
- Un lot = un job Slurm à 1 GPU et 8 cœurs, dans lequel tournent N processus VASP (`run_conc.slurm`) :
  Slurm ne partage pas les GPU entre jobs sur cette machine.
- Deux modes : `nomps` (partage de la GPU par tranches de temps, par défaut) et `mps` (démon NVIDIA
  MPS propre au job, dans `/tmp/mps_<job>`, qui laisse les noyaux des N processus s'exécuter ensemble).
- Calculs : `pbe_sp` Si 8 at. et MgO 32 at., `r2scan_sp` Al 32 at. avec N = 1 (MPS seul), 2, 4, 8 ;
  `r2scan_sp` Si 64 at. avec N = 1, 2, 4 (limité par la mémoire hôte). 26 lots, 100 calculs,
  3,9 h au plus sur 1 GPU.
- Mesures : gain de débit N × t_ref / T_lot (idéal : N), ralentissement de chaque calcul,
  temps par pas SCF, mémoire et utilisation GPU, écart d'énergie avec la référence (doit être nul).
