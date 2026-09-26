# Performances de VASP GPU sur vm1-ic2mp

Ce dépôt rassemble le protocole et les tableaux de résultats d'une étude de performance
de VASP 6.5.1 (version GPU) sur la machine partagée **vm1-ic2mp** (2 × NVIDIA H100).
Les calculs portent sur des structures de [Materials Project](https://next-gen.materialsproject.org)
de 1 à 80 atomes. Deux types de calcul sont testés : point simple (single point) et optimisation
de géométrie. Chacun suit les deux méthodologies de Materials Project : **PBE** (GGA/GGA+U)
et **r2SCAN**.

> **État : protocole proposé, en attente de validation.** Aucun calcul de l'étude n'a encore
> été lancé. Seul un test de validation de la chaîne (5 petits calculs, 1 à 2 atomes) a tourné.

## Contenu du dépôt

| Dossier | Contenu |
|---|---|
| [`benchmark/`](benchmark/) | Scripts de l'étude : récupération des structures MP, génération des entrées, soumission Slurm, analyse. Installation : `benchmark/setup.sh` |
| [`vaspilot/`](vaspilot/) | [VASPilot](https://github.com/JiaxuanLiu-Arsko/VASPilot) adapté à cette machine : agents qui pilotent VASP en langage naturel. Installation : `vaspilot/install.sh` |
| [`results/`](results/) | Tableaux de résultats finaux, un par phase |

Les calculs eux-mêmes (OUTCAR, etc.) ne sont pas versionnés : ils restent sur vm1-ic2mp.
Les POTCAR, sous licence VASP, ne sont jamais versionnés.

## Questions étudiées

1. Comment le temps de calcul évolue-t-il avec le nombre d'atomes, de 1 à 80 ?
2. Combien coûte r2SCAN par rapport à PBE, par pas SCF et au total ?
3. Combien coûte une optimisation complète par rapport à un point simple ?
4. Que rapporte le passage de 1 à 2 GPU, et vaut-il mieux répartir les points k (KPAR=2)
   ou les bandes et ondes planes (KPAR=1) ?
5. Quelle mémoire GPU faut-il selon la taille du système ?
6. Les temps sont-ils reproductibles d'un job à l'autre ?
7. Les énergies obtenues sont-elles cohérentes avec celles publiées par Materials Project ?

## Machine

| Élément | Valeur |
|---|---|
| GPU | 2 × NVIDIA H100 NVL, 94 Go chacun (pilote 595.84) |
| CPU | AMD EPYC 7662, 32 cœurs attribués à la VM |
| Mémoire | 62 Go |
| Ressources par GPU (Slurm) | 16 cœurs, ~28 Go de RAM, pas de limite de temps |
| Logiciel | VASP 6.5.1, NVHPC 26.3, OpenMPI HPC-X, Intel MKL |
| Exécution | 1 rang MPI par GPU, `OMP_NUM_THREADS=1` |

## Méthodes

Les entrées sont générées avec les jeux de paramètres de Materials Project implémentés dans
pymatgen, sans modification des paramètres physiques.

| Série | Jeu pymatgen | Paramètres principaux |
|---|---|---|
| `pbe_sp` | `MPStaticSet` | PBE, U de MP pour les oxydes et fluorures de Co, Cr, Fe, Mn, Mo, Ni, V ; ENCUT 520 eV ; 100 points k par Å⁻³ réciproque ; ISPIN 2 |
| `pbe_relax` | `MPRelaxSet` | idem, ISIF 3, ALGO Fast, 64 points k par Å⁻³ réciproque, EDIFF 5·10⁻⁵ eV par atome |
| `r2scan_sp` | `MPScanStaticSet` | METAGGA R2SCAN, ENCUT 680 eV, KSPACING de 0,22 à 0,44 Å⁻¹ selon le gap MP, EDIFF 10⁻⁵ |
| `r2scan_relax` | `MPScanRelaxSet` | idem, ISIF 3, ALGO All, EDIFFG −0,02 eV/Å |

Écarts à la méthodologie MP, identiques pour tous les calculs :

- **POTCAR** : le jeu PBE_54 installé sur la machine sert aussi pour PBE. MP utilise un jeu PBE
  plus ancien, identique pour presque tous les éléments. W_pv, absent, serait remplacé par W_sv.
- **Écritures désactivées** : WAVECAR, CHGCAR, AECCAR, LOCPOT et ELFCAR, pour mesurer le calcul
  et non les entrées/sorties.
- **Parallélisation** : NCORE est retiré, car la version GPU l'impose à 1. KPAR dépend de la variante.
- **Petites grilles** : quand la grille a moins de 4 points k, ISMEAR −5 (tétraèdres) est remplacé
  par ISMEAR 0 avec SIGMA 0,05, comme le fait custodian chez MP.

Les optimisations partent des structures MP, déjà relaxées en PBE. En PBE, elles convergent donc
en peu de pas. En r2SCAN, elles demandent quelques pas de plus. Les résultats donnent le temps
total et le temps par pas ionique.

## Composés

### Séries d'échelle

Un même matériau en supercellules de taille croissante. À chimie constante, on isole l'effet
du nombre d'atomes.

| Matériau | mp-id | Type | Tailles (atomes) |
|---|---|---|---|
| Si diamant | mp-149 | semi-conducteur | 2 (primitive), 8, 16, 32, 64 |
| Al cfc | mp-134 | métal | 1 (primitive), 4, 32, 64 |
| MgO sel gemme | mp-1265 | isolant | 2 (primitive), 8, 16, 32, 64 |

Au total, 14 structures. En supercellule, la densité de points k par atome est conservée, comme
dans la méthodologie MP : le nombre de points k diminue quand la maille grandit.

### Jeu de diversité

Des matériaux réels choisis dans Materials Project, pour couvrir des chimies et des tailles de
maille variées. Pour chaque taille (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72
et 80 atomes), le script retient deux composés :

- un non-métal (gap MP > 0,3 eV) ;
- un métal.

Critères de sélection :

- composés stables (sur l'enveloppe convexe de MP) ;
- au plus 4 éléments ;
- ni terres rares, ni actinides, ni gaz rares, ni W ;
- sélection déterministe : le plus petit mp-id qui remplit les critères.

Au plus 36 structures. **La liste exacte sera ajoutée ici pour validation avant tout calcul.**
Elle est produite par une simple requête à l'API MP, qui nécessite une clé API.

| Taille | Non-métal | Métal |
|---|---|---|
| 1 … 80 | *à compléter* | *à compléter* |

## Utilisation des GPU

La machine est partagée. Par défaut, un seul calcul de l'étude tourne à la fois, ce qui laisse
le second GPU aux autres utilisateurs. Les séries à 1 GPU et à 2 GPU ne sont jamais soumises
en même temps.

| Variante | GPU | Rangs MPI | KPAR | Rôle |
|---|---|---|---|---|
| `g1` | 1 | 1 | 1 | référence, toutes les phases |
| `g2k1` | 2 | 2 | 1 | 2 GPU, bandes et ondes planes réparties |
| `g2k2` | 2 | 2 | 2 | 2 GPU, un groupe de points k par GPU (seulement s'il y a au moins 2 points k) |

## Plan

| Phase | Contenu | GPU | Calculs (env.) |
|---|---|---|---|
| 0 | Validation de la chaîne sur 1 à 2 atomes | 1 | 5 (fait) |
| 1 | Reproductibilité : Si 8, MgO 32 et Al 32 en `pbe_sp` et `r2scan_sp`, 3 répétitions chacun | 1 | 18 |
| 2 | Points simples, toutes les structures, PBE et r2SCAN | 1 | ~100 |
| 3 | Optimisations, toutes les structures, PBE et r2SCAN | 1 | ~100 |
| 4 | Passage à 2 GPU : séries d'échelle en point simple, PBE et r2SCAN, `g2k1` et `g2k2` | 2 | ~50 |
| 5 | Optimisations à 2 GPU sur les trois structures de 64 atomes (option) | 2 | 6 à 12 |

Chaque phase est soumise séparément, du plus petit au plus grand système. Les durées de la phase 2
permettront d'estimer celles des phases suivantes avant de les lancer. Les phases 4 et 5 occupent
toute la machine : elles seront lancées le soir ou le week-end, après avoir prévenu les autres
utilisateurs.

## Mesures relevées

Pour chaque calcul :

- **Taille du problème** : nombre d'atomes, NKPTS, NBANDS, NPLWV, NELECT, ISPIN.
- **Temps** :
  - temps total (Elapsed time de l'OUTCAR et temps mur du job) ;
  - temps médian par pas SCF (lignes LOOP) ;
  - temps moyen par pas ionique (lignes LOOP+) ;
  - nombre de pas SCF et de pas ioniques.
- **Mémoire** : pic de mémoire GPU et utilisation GPU moyenne (relevés `nvidia-smi` toutes les
  2 s), mémoire hôte maximale.
- **Énergie** : énergie par atome et écart à l'énergie MP non corrigée de la même fonctionnelle,
  en meV/atome.
- **Contexte** : autres jobs présents sur la machine au démarrage, car ils peuvent influencer
  les temps.

## Résultats

*Tableaux à venir, un par phase.*

| Série | Composé | Atomes | NKPTS | NBANDS | Pas SCF | Pas ioniques | s / pas SCF | Temps total (s) | Mémoire GPU (Go) | ΔE vs MP (meV/at) |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |
