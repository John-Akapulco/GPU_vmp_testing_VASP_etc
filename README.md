# Performances de VASP GPU sur vm1-ic2mp

Ce dépôt rassemble le protocole et les tableaux de résultats d'une étude de performance
de VASP 6.5.1 (version GPU) sur la machine partagée **vm1-ic2mp** (2 × NVIDIA H100).
Les calculs portent sur des structures de [Materials Project](https://next-gen.materialsproject.org)
de 1 à 64 atomes : pour l'instant, seules les séries d'échelle (Si, Al, MgO) sont retenues. Deux types de calcul sont testés : point simple (single point) et optimisation
de géométrie. Chacun suit les deux méthodologies de Materials Project : **PBE** (GGA/GGA+U)
et **r2SCAN**.

> **État : protocole proposé, en attente de validation.** Aucun calcul de l'étude n'a encore
> été lancé. Seul un test de validation de la chaîne (5 petits calculs, 1 à 2 atomes) a tourné.

## Contenu du dépôt

| Dossier | Contenu |
|---|---|
| [`benchmark/`](benchmark/) | Scripts de l'étude : récupération des structures MP, génération des entrées, soumission Slurm, analyse. Installation : `benchmark/setup.sh` |
| [`vaspilot/`](vaspilot/) | **Mis de côté, hors de l'étude.** [VASPilot](https://github.com/JiaxuanLiu-Arsko/VASPilot) adapté à cette machine : agents qui pilotent VASP en langage naturel. Voir la note ci-dessous |
| [`results/`](results/) | Tableaux de résultats finaux, un par phase |

Les calculs eux-mêmes (OUTCAR, etc.) ne sont pas versionnés : ils restent sur vm1-ic2mp.
Les POTCAR, sous licence VASP, ne sont jamais versionnés.

### VASPilot mis de côté

L'étude n'utilise pas VASPilot : les scripts de `benchmark/` suffisent et ne font appel à aucun LLM.
VASPilot demande un LLM. Avec une API en ligne, chaque tâche est facturée par le fournisseur, et les
consignes ainsi que les résultats renvoyés par les outils (structures, paramètres, énergies, chemins)
sortent de la machine. Un LLM local éviterait ces deux points, mais il occuperait l'un des deux GPU
partagés. L'adaptation reste dans `vaspilot/` pour un usage éventuel. Elle est installée sur la machine,
mais ses serveurs ne sont pas lancés et aucune clé LLM n'est configurée.

## Questions étudiées

1. Comment le temps de calcul évolue-t-il avec le nombre d'atomes, de 1 à 64 ?
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

**Périmètre actuel : les séries d'échelle seules (14 structures).** Le jeu de diversité est
conservé ci-dessous pour une étape ultérieure, mais il n'est pas calculé à ce stade.

### Séries d'échelle (retenues)

Un même matériau en supercellules de taille croissante. À chimie constante, on isole l'effet
du nombre d'atomes.

| Matériau | mp-id | Type | Tailles (atomes) |
|---|---|---|---|
| Si diamant | mp-149 | semi-conducteur | 2 (primitive), 8, 16, 32, 64 |
| Al cfc | mp-134 | métal | 1 (primitive), 4, 32, 64 |
| MgO sel gemme | mp-1265 | isolant | 2 (primitive), 8, 16, 32, 64 |

Au total, 14 structures. En supercellule, la densité de points k par atome est conservée, comme
dans la méthodologie MP : le nombre de points k diminue quand la maille grandit.

### Jeu de diversité (reporté, non calculé)

Des matériaux réels choisis dans Materials Project, pour couvrir des chimies et des tailles de
maille variées. Pour chaque taille (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72
et 80 atomes), le script retient un non-métal (gap MP > 0,3 eV) et un métal (gap MP nul).

Critères de sélection :

- composés stables (sur l'enveloppe convexe de MP) ;
- au plus 4 éléments ;
- ni terres rares, ni actinides, ni gaz rares, ni W ;
- sélection déterministe : le plus petit mp-id qui remplit les critères.

Liste obtenue le 26/09/2026 avec `01_fetch_structures.py` (35 structures). Gaps en eV, en PBE, selon MP.

| Atomes | Non-métal (mp-id, gap) | Métal (mp-id) |
|---|---|---|
| 1 | aucun | Pd (mp-2) |
| 2 | Si (mp-149, 0,61), déjà dans la série d'échelle | Fe (mp-13), magnétique |
| 3 | CdF₂ (mp-241, 2,90) | Ti (mp-72) |
| 4 | PtS (mp-288, 0,38) | Be (mp-87) |
| 6 | Cu₂O (mp-361, 0,51) | SrIr₂ (mp-318) |
| 8 | N₂ (mp-154, 7,38) | GeIr (mp-208) |
| 10 | MgP₄ (mp-384, 0,73) | Ti₂O₃ (mp-458) |
| 12 | B (mp-160, 1,43) | BaZn₅ (mp-303) |
| 16 | KAs (mp-713, 0,67) | ReO₃ (mp-190) |
| 20 | MgB₄ (mp-365, 0,36) | Mg₃Ru₂ (mp-625) |
| 24 | SeO₂ (mp-726, 3,29) | NbSe₃ (mp-525) |
| 32 | S (mp-77, 2,58) | NaB₁₅ (mp-2315) |
| 40 | Ca₃N₂ (mp-844, 1,11) | Tl₂O₃ (mp-1658) |
| 48 | LiNb₃O₈ (mp-3368, 2,97) | TiMnSi₂ (mp-21606), magnétique |
| 56 | Na₃P₁₁ (mp-473, 1,83) | BaCdSnS₄ (mp-12306) |
| 64 | RbSi (mp-1074, 1,35) | TlTe (mp-2048) † |
| 72 | P₂PbO₆ (mp-3476, 4,48) | Hf₃(VGa₂)₂ (mp-1200384) † |
| 80 | Mn₇SiO₁₂ (mp-3224, 0,49), magnétique, GGA+U † | Na₃Li₃Ti₂F₁₂ (mp-14457), magnétique † |

† Pas d'énergie r2SCAN publiée par MP : comparaison à MP possible en PBE seulement.

Remarques :

- « Métal » signifie ici gap PBE nul dans MP. Certains composés classés ainsi, comme BaCdSnS₄ ou
  Na₃Li₃Ti₂F₁₂, sont en réalité des isolants mal décrits par PBE.
- Seul Mn₇SiO₁₂ relève de GGA+U. Ti₂O₃ n'en relève pas, car MP n'applique pas de U à Ti.

## Utilisation des GPU

La machine est partagée. Par défaut, un seul calcul de l'étude tourne à la fois, ce qui laisse
le second GPU aux autres utilisateurs. Les séries à 1 GPU et à 2 GPU ne sont jamais soumises
en même temps.

| Variante | GPU | Rangs MPI | KPAR | Rôle |
|---|---|---|---|---|
| `g1` | 1 | 1 | 1 | référence, phases 1 à 3 |
| `g2k1` | 2 | 2 | 1 | 2 GPU, bandes et ondes planes réparties |
| `g2k2` | 2 | 2 | 2 | 2 GPU, un groupe de points k par GPU (seulement s'il y a au moins 2 points k) |

## Plan

| Phase | Contenu | GPU | Calculs |
|---|---|---|---|
| 0 | Validation de la chaîne sur 1 à 2 atomes | 1 | 5 (fait) |
| 1 | Reproductibilité : Si 8, MgO 32 et Al 32 en `pbe_sp` et `r2scan_sp`, 3 répétitions chacun | 1 | 18 |
| 2 | Points simples, séries d'échelle, PBE et r2SCAN | 1 | 28 |
| 3 | Optimisations, séries d'échelle, PBE et r2SCAN | 1 | 28 |
| 4 | Passage à 2 GPU : séries d'échelle en point simple, PBE et r2SCAN, `g2k1` et `g2k2` | 2 | ≤ 56 |
| 5 | Optimisations à 2 GPU sur les trois structures de 64 atomes (option) | 2 | 6 à 12 |

Toutes les phases portent sur les 14 structures des séries d'échelle (option `--family scaling`
de `02_make_inputs.py` et `03_submit.py`). Le jeu de diversité pourra faire l'objet d'une phase
ultérieure.

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
