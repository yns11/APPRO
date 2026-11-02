# Performance : mesures de référence

Réponse au point F33 de l'audit du 29/10/2026 : **mesurer avant de fixer un objectif**. Les chiffres ci-dessous sont
ceux du jeu de démonstration sur la machine de développement ; ils servent de **point de comparaison** d'une version
à l'autre, pas de SLO. Les refaire :

```bash
python scripts/bench.py                          # seed, approvisionneur QUENTIN, 20 itérations
python scripts/bench.py --runs 50 --planner ""   # tous les articles
APPRO_SEED_DIR=/chemin/extraction python scripts/bench.py   # une extraction réelle au format CSV du seed
```

Le script mesure le moteur seul (`run_mrp`), les réponses de l'API (latence p50 / p95 / max et taille), une écriture
suivie du recalcul qu'elle déclenche, la taille d'un résultat en cache et la mémoire du processus.

## Relevé du 31/10/2026 (16 articles, 772 créneaux, 483 réceptions, 10 itérations)

| Mesure | p50 | p95 | Taille |
|---|---:|---:|---:|
| moteur `run_mrp` (sans cache) | 171 ms | 177 ms | 3,4 Mo en mémoire |
| tableau de bord (cache chaud) | 56 ms | 897 ms¹ | 337 ko |
| tableau d'approvisionnement, page de 20 | 169 ms | 214 ms | 897 ko |
| fiche article (projection) | 114 ms | 126 ms | 58 ko |
| flux du jour | 98 ms | 212 ms | 18 ko |
| file d'exceptions | 120 ms | 179 ms | 31 ko |
| OTD fournisseurs, 12 mois | 166 ms | 227 ms | 226 ko |
| export Excel simulation | 12,4 s | 13,1 s | 672 ko |
| saisie d'une cellule + recalcul du tableau de bord | 1,9 s | 2,3 s | 337 ko |

¹ le p95 du tableau de bord inclut la première réponse (cache froid) : le calcul complet, puis les lectures en cache.

Mémoire Python : 15 Mo courants, pic 77 Mo. Cache : 1 résultat de 3,8 Mo pour un **budget de 256 Mo**
(`APPRO_CACHE_BUDGET_MB`, éviction du résultat le moins récemment utilisé au-delà ; un résultat plus grand que le
budget est servi sans être gardé). `/api/health` expose `cache.entries`, `cache.bytes`, `cache.budget`.

Relevé du 01/11/2026 après la réécriture de l'OTD (`services/otd.py` : grille de jours ouvrés, lignes évaluées une
fois par version des données et gardées en cache) : `GET /api/performance/otd` **17 ms** à chaud (166 ms avant) ;
construction des lignes 850 ms pour 38 600 créneaux et 24 000 réceptions (jeu de démonstration répliqué 50 fois),
rapport 170 ms sur 19 000 lignes. La fiche fournisseur × mois (`/otd/sheet`) et le PDF d'arrêté relisent ces mêmes
lignes en cache : quelques millisecondes pour la fiche, le PDF portefeuille dépend du nombre de fournisseurs (une
page chacun).

## Ce que les chiffres disent

* Le **moteur** coûte ~11 ms par article sur 400 jours : 500 articles ≈ 5 s à froid, servis ensuite depuis le
  cache jusqu'à la prochaine écriture ou synchronisation. La **version des données** est désormais incrémentée dans
  la transaction de chaque écriture ; chaque écriture invalide donc tout le cache, d'où les ~2 s « saisie +
  recalcul » (recalcul du périmètre entier). Une invalidation ciblée par article reste la prochaine marche si ce
  délai devient gênant sur un grand périmètre.
* L'**export Excel** est de loin le plus coûteux (openpyxl, une formule par cellule) : à lancer sur un périmètre
  filtré, jamais sur tout le portefeuille en semaine.
* Les **tailles de réponse** sont bornées par la pagination (20 articles par page du tableau) et par la fenêtre
  affichée des lignes de détail (`since` dans `lane_out`).

## Côté navigateur

Le client est désormais **découpé** : chaque page est chargée à la demande (`React.lazy`), les bibliothèques vont dans
des morceaux stables (`vendor-react`, `vendor`, `vendor-icons`, `vendor-dates`) et la bibliothèque de graphiques
dans `vendor-charts`, téléchargée seulement par les pages qui tracent une courbe. Build du 31/10/2026 :

| Morceau | Taille | gzip |
|---|---:|---:|
| `index` (coquille, menu, contexte) | 39 ko | 14 ko |
| `vendor-react` (React, routeur, requêtes) | 199 ko | 63 ko |
| `vendor` (autres bibliothèques) | 93 ko | 33 ko |
| `vendor-charts` (graphiques, pages concernées seulement) | 333 ko | 84 ko |
| pages : 5 à 58 ko chacune | | |

Avant : un seul fichier de 1 039 ko (297 ko gzip) chargé à l'ouverture. Le premier écran charge aujourd'hui la
coquille, `vendor-react`, `vendor` et la page demandée (~110 ko gzip pour le tableau de bord sans la courbe de
valeur, qui tire `vendor-charts` à part).

## Prochaines mesures utiles

Volumes réels (quelques centaines d'articles, dizaines de milliers de créneaux) et vingt sessions simultanées sur
l'App déployée : `scripts/bench.py` accepte une extraction au format du seed ; les p95 de l'App se lisent dans les
journaux Databricks Apps.
