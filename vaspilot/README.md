# VASPilot sur vm1-ic2mp

[VASPilot](https://github.com/JiaxuanLiu-Arsko/VASPilot) pilote VASP à l'aide d'agents
(CrewAI) qui appellent des outils exposés par un serveur MCP. Ces outils recherchent des
structures dans Materials Project, génèrent les entrées, soumettent à Slurm, suivent les jobs
et lisent les résultats. On décrit la tâche en langage naturel dans une interface web.

Ce dossier contient l'adaptation de VASPilot à vm1-ic2mp (2 × H100, Slurm).

## Adaptations

| Problème sur vm1-ic2mp | Correction |
|---|---|
| La comptabilité Slurm est désactivée : `sacct` ne renvoie rien, et VASPilot ne savait jamais si un job avait réussi | `slurm.sh` écrit le code de retour de VASP dans `slurm_exit_code`. VASPilot le lit, avec l'OUTCAR, pour classer le job en `completed` ou `failed` |
| Aucun script Slurm fourni ; les INCAR par défaut sont réglés pour CPU (`NCORE=4`) | `mcp/attachment/slurm.sh` pour GPU (1 rang MPI par GPU, `vasp_ncl` choisi automatiquement si LSORBIT est activé) ; NCORE retiré des INCAR par défaut, KPAR=1 |
| Pas de choix du nombre de GPU | La soumission passe `--gres=gpu:N --ntasks=N` |
| Pas de méthodologie Materials Project | Nouvel outil MCP `vasp_mp_calculation` : PBE ou r2SCAN, point simple ou optimisation, 1 ou 2 GPU. Ce sont les mêmes jeux que dans `benchmark/` |
| Un serveur d'embeddings est obligatoire | L'embedder devient facultatif. Sans lui, la mémoire des agents est désactivée et une seule clé LLM suffit |
| Plantage si `--config` est un chemin relatif | Chemin converti en absolu |
| Les versions actuelles de crewai, fastmcp et mcp cassent VASPilot | Dépendances figées à la mi-août 2025 (`requirements-lock.txt`) |

Toutes les modifications du code sont dans `vaspilot-vm1.patch`, appliqué sur le commit
`2d9b2df` de VASPilot, branche locale `vm1-ic2mp-gpu`.

`slurm.sh` relève aussi la mémoire et l'utilisation GPU (`gpu_monitor.csv`) ainsi que le temps
mur (`timing.json`). Les calculs lancés par VASPilot sont donc exploitables pour le benchmark.

## Installation

```bash
~/testing_VASP/GPU_vmp_testing_VASP_etc/vaspilot/install.sh
```

## Clés

Deux clés sont nécessaires. Elles ne sont jamais versionnées : les scripts de démarrage les
insèrent dans `configs/*.yaml`, fichiers ignorés par git et lisibles par toi seul.

```bash
# Materials Project : https://next-gen.materialsproject.org/api
echo "TA_CLE_MP" > ~/.mp_api_key && chmod 600 ~/.mp_api_key

# LLM : un fichier ~/.vaspilot_llm (chmod 600), au choix :
# a) API Anthropic
LLM_MODEL=anthropic/claude-sonnet-5
LLM_API_KEY=sk-ant-...
# b) API compatible OpenAI (DeepSeek, Qwen, serveur vLLM local…)
LLM_MODEL=openai/deepseek-chat
LLM_API_KEY=...
LLM_BASE_URL=https://api.deepseek.com/v1
```

Chaque tâche donnée à VASPilot consomme des appels au LLM, facturés par le fournisseur.

## Démarrage

```bash
cd ~/testing_VASP/GPU_vmp_testing_VASP_etc/vaspilot
./start_mcp_server.sh      # outils VASP, port 8933
./start_crew_server.sh     # interface web, port 51293
./stop_servers.sh          # arrêt des deux
```

Les deux serveurs n'écoutent qu'en local (127.0.0.1). L'interface web n'a pas
d'authentification et donne accès aux fichiers du dossier. Pour l'ouvrir depuis ton poste,
passe par un tunnel SSH :

```bash
ssh -L 51293:localhost:51293 gfrapper@vm1-ic2mp
# puis http://localhost:51293 dans le navigateur
```

## Usage pour le benchmark

Exemples de demandes :

- « Search Materials Project for stable GaAs, then run a single-point calculation with the
  Materials Project r2SCAN methodology on 1 GPU and report the total energy and the wall time. »
- « Make 2x2x2 and 3x3x3 supercells of Si (mp-149) and relax them with the Materials Project
  PBE methodology on 1 GPU. »

L'interface traite une tâche à la fois (`--max-concurrent-tasks 1`). L'agent VASP est réglé
pour utiliser 1 GPU, sauf demande explicite de 2.

## État des tests (26/09/2026)

| Test | Résultat |
|---|---|
| Installation, démarrage du serveur MCP, 18 + 1 outils exposés | OK |
| Démarrage de l'interface web | OK |
| Génération des entrées MP (PBE et r2SCAN, 1 et 2 GPU) sans soumission | OK |
| Détection de la fin d'un job sans `sacct`, à partir d'un calcul terminé | OK |
| Tâche complète pilotée par le LLM, avec calcul sur GPU | À faire : nécessite les clés et ton accord pour lancer un calcul |
