# Scan NSIM, VASP 6.5.1 GPU, 1 × H100

Une exécution par valeur (r1) ; temps par pas électronique : moyenne des lignes LOOP après les 3 premiers pas (HSE : 4, pas sans échange exact). Gain : temps NSIM = 4 / temps NSIM. ΔE : écart maximal d'énergie entre toutes les valeurs de NSIM d'un même cas.

## MgO_032, PBE (NBANDS 144, 12 points k)

| NSIM | s / pas | gain vs 4 | temps total (s) | mémoire GPU (Go) | utilisation GPU (%) |
|---|---|---|---|---|---|
| 4 | 7.904 | 1.00 | 168.0 | 4.7 | 49 |
| 8 | 7.704 | 1.03 | 163.6 | 4.8 | 49 |
| 16 | 7.675 | 1.03 | 163.6 | 4.9 | 50 |
| 32 | 7.666 | 1.03 | 163.2 | 5.1 | 50 |
| 64 | 7.768 | 1.02 | 165.3 | 5.5 | 51 |
| 128 | 7.676 | 1.03 | 164.1 | 5.9 | 49 |

NSIM le plus rapide : 32. ΔE max entre runs : 2.00e-08 eV/cellule.

## LiN3_144, PBE (NBANDS 440, 8 points k)

| NSIM | s / pas | gain vs 4 | temps total (s) | mémoire GPU (Go) | utilisation GPU (%) |
|---|---|---|---|---|---|
| 4 | 45.039 | 1.00 | 952.1 | 10.9 | 74 |
| 8 | 43.867 | 1.03 | 930.2 | 11.1 | 75 |
| 16 | 43.545 | 1.03 | 924.9 | 11.1 | 74 |
| 32 | 42.703 | 1.05 | 910.0 | 13.1 | 75 |
| 64 | 42.627 | 1.06 | 915.7 | 15.9 | 74 |
| 128 | 42.810 | 1.05 | 931.5 | 19.8 | 74 |

NSIM le plus rapide : 64. ΔE max entre runs : 1.49e-05 eV/cellule (au-delà de 1E-6 eV, mais SCF non convergée en NELM pas : |dE| au dernier pas jusqu'à 1.3e-04 eV ; écart dû au chemin de convergence, pas à NSIM).

## Si_216, PBE (NBANDS 606, 1 points k)

| NSIM | s / pas | gain vs 4 | temps total (s) | mémoire GPU (Go) | utilisation GPU (%) |
|---|---|---|---|---|---|
| 4 | 19.758 | 1.00 | 501.3 | 12.1 | 64 |
| 8 | 19.592 | 1.01 | 498.3 | 12.7 | 64 |
| 16 | 19.740 | 1.00 | 502.9 | 14.7 | 64 |
| 32 | 19.732 | 1.00 | 505.7 | 18.0 | 64 |
| 64 | 20.258 | 0.98 | 513.3 | 24.2 | 65 |
| 128 | 20.842 | 0.95 | 529.1 | 39.3 | 65 |

NSIM le plus rapide : 8. ΔE max entre runs : 3.52e-05 eV/cellule (au-delà de 1E-6 eV, mais SCF non convergée en NELM pas : |dE| au dernier pas jusqu'à 4.8e-05 eV ; écart dû au chemin de convergence, pas à NSIM).

## Conditions

18 calcul(s) terminé(s) sur 18 ont démarré avec d'autres jobs sur le nœud (GPU partagé ; colonne other_jobs_on_node de nsim_scan.csv). Les écarts de quelques % entre valeurs de NSIM sont du même ordre que l'effet du partage : à confirmer sur GPU seul.

## Échecs

Aucun.

## Recommandation

À rédiger après examen des résultats.
