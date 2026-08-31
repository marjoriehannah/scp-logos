# Prototype ScrapeGraphAI → batch de prospection SCP

Chaîne complète, fonctionnelle : **une liste de sites web d'entreprises → 4 fichiers
xlsx au format Salesforce Inspector Reloaded**, stratifiés chaud / tiède / volume /
à confirmer / exclus, selon la spec de l'installateur actif.

C'est le maillon que le pipeline actuel fait à la main : lire le site d'un prospect,
y repérer un signal (rénovation, surchauffe, vis-à-vis), noter le décideur et le
téléphone. [ScrapeGraphAI](https://github.com/ScrapeGraphAI/Scrapegraph-ai) fait
lire la page par un LLM contraint par un schéma, donc la sortie est directement
exploitable — pas du texte libre à retraiter.

## Ce que ça fait, concrètement

```
prospects.csv (Name, Website, ...)
        │
        ▼
 ScrapeGraphAI · SmartScraperGraph        ← 1 appel LLM par site, schéma imposé
        │   téléphone, adresse, effectif, décideurs nommés,
        │   citations sur les surfaces vitrées, signaux datés
        ▼
 Normalisation Salesforce                 ← E.164, Title Case, picklist Industry
        │
        ▼
 Scoring selon scoring_weights de la spec ← chaud / tiède / volume
        │   + exclusions (concurrents, doublons, cessations)
        │   + hors-cible (holding, structure sans local)
        │   + à confirmer (zone, ambiguïtés)
        ▼
 2026-08-31_VALIDES.xlsx · _VOLUME.xlsx · _A_CONFIRMER.xlsx · _EXCLUS_ET_HORS_CIBLE.xlsx
 + _stratif_complete.csv (traçabilité : score et motif de chaque lead)
```

Les colonnes sont exactement celles de la skill `scp-lead-pipeline` v2, y compris
`Lead_Panier__c`, `Lead_Confiance__c`, `Lead_Signal__c`, `Lead_Angle__c`,
`Lead_Decideur__c`, `Lead_Decideur_Source__c` et `Lead_Question_Verif__c`.

## Essai immédiat, sans rien installer de lourd

Le mode `fixtures` rejoue des extractions enregistrées : toute la chaîne tourne
sans LLM, sans réseau, sans playwright.

```bash
cd tools/scrapegraph-prospects
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-core.txt

python -m scpscrape.cli \
  --spec config/exemple-installateur.yaml \
  --input fixtures/prospects.csv \
  --provider fixtures --fixtures-dir fixtures/extractions \
  --outdir sorties/exemple-sa
```

Sortie attendue sur les 8 prospects d'exemple :

```
Batch Exemple SA — 2026-08-31 — 8 prospects analysés
  chauds        : 1     Hôtel Lac Panorama    (rénovation 2026 + surchauffe)
  tièdes        : 2     Clinique des Alpes, Garage Mont-Blanc
  volume        : 1     Ibis Annecy Centre    (chaîne intégrée → décision au siège)
  à confirmer   : 2     zone indéterminée / hors zone
  exclus/hors-c.: 2     Miroiterie du Léman (concurrent), holding non opérationnelle
```

## Utilisation réelle

### Option A — Ollama, gratuit, rien ne sort de la machine

À privilégier : les pages scrapées contiennent des données personnelles
(noms de dirigeants, emails), qui ne partent alors chez aucun tiers.

```bash
pip install -r requirements.txt      # Python >= 3.12 requis par scrapegraphai
playwright install chromium

ollama serve &
ollama pull llama3.2

python -m scpscrape.cli \
  --spec config/exemple-installateur.yaml \
  --input mes-prospects.csv \
  --provider ollama \
  --outdir batchs-livres/exemple-sa \
  --dedup exports-salesforce/comptes-deja-touches.csv
```

### Option B — OpenAI, payant, extraction plus fiable

```bash
export OPENAI_API_KEY=sk-...
python -m scpscrape.cli --spec ... --input ... --provider openai --outdir ...
```

### Fichier d'entrée

Un CSV UTF-8. Seule `Website` est obligatoire ; tout le reste est facultatif et,
**s'il est renseigné, il prime sur ce que le LLM trouve sur le site**.

```csv
Id,Name,Website,Phone,BillingPostalCode,BillingCity,BillingCountry
,Hôtel Lac Panorama,https://www.hotel-lac-panorama.fr,,,,France
0011t00000AbCdEAAV,Clinique des Alpes,https://www.clinique-des-alpes.fr,0450332211,74000,ANNECY,France
```

### Options utiles

| Option | Effet |
|---|---|
| `--limit 10` | ne traite que les 10 premières lignes (essai de calibrage) |
| `--dedup fichier.csv` | comptes déjà touchés → panier EXCLU (répétable) |
| `--date 2026-08-31` | date du batch dans les noms de fichiers et les `Description` |
| `--delay 3` | secondes entre deux sites (défaut 2) |
| `--no-headless` | navigateur visible, pour comprendre un site qui résiste |
| `--verbose` | logs ScrapeGraphAI détaillés |

## Comment le scoring décide

L'ordre compte : le premier verdict qui tombe l'emporte.

1. **EXCLU** — concurrent (vitrerie, miroiterie, poseur de films, marques
   concurrentes), doublon via `--dedup`, cessation (`etat_administratif = C`
   fourni en entrée), concurrent listé dans `competitors_to_avoid`.
2. **HORS-CIBLE** — SCI patrimoniale, holding, snack/fast-food, ou 3 indices
   ou plus de « structure sans local » (pas de téléphone / mobile seul / pas de
   site / pas d'adresse / 0-1 salarié).
3. **À CONFIRMER** — hors zone de la spec, zone indéterminée, ou exactement
   2 indices « structure sans local ». Chaque ligne porte la question à poser
   dans la première minute d'appel.
4. **CHAUD / TIÈDE / VOLUME** — somme des `scoring_weights` de la spec, comparée
   à `threshold_chaud` et `threshold_tiede`.

Deux règles méritent d'être connues :

- **Un même poids ne compte qu'une fois.** « rénovation » et « extension »
  partagent `recent_renovation` : deux mentions ne valent pas 4 points.
- **Une chaîne intégrée est toujours reversée en volume**, même très bien scorée :
  la décision se prend au siège, pas dans l'établissement.

Le fichier `_stratif_complete.csv` donne pour chaque prospect son score, le détail
des points et le signal retenu — de quoi discuter un classement plutôt que le subir.

## Calibrer sur un installateur réel

Le fichier `config/exemple-installateur.yaml` est un exemple. Pour un vrai
installateur, copier sa spec (format `scp-client-spec`) et l'utiliser telle quelle :
le prototype lit `zone.departments_fr`, `zone.cantons_ch`,
`target_segments.preferred_sectors`, `target_segments.company_size`,
`products.primary`, `competitors_to_avoid` et `scoring_weights`.

Changer les seuils de la spec change le batch : aucun seuil n'est écrit en dur
dans le code. C'est le point d'accroche de `scp-feedback-calibration` — après un
bilan mensuel, on ajuste les poids dans le YAML, on rejoue, on compare.

## Coût et durée

- **Ollama** : gratuit, ~20 à 60 s par site sur une machine de bureau.
- **OpenAI** : un appel `gpt-4o-mini` par site, sur une page convertie en markdown
  puis découpée — comptez de l'ordre de 15 000 à 40 000 tokens d'entrée par site
  selon son poids. Multipliez par le tarif en vigueur et par votre volume avant de
  lancer 60 sites ; commencez toujours par `--limit 5`.
- Le `--delay` (2 s par défaut) domine la durée sur un gros batch : 60 sites =
  au moins 2 minutes d'attente volontaire, plus le temps d'extraction.

## Limites connues

- **Ce n'est pas une source de leads.** Il faut lui donner une liste d'URLs. Il
  enrichit et trie, il ne découvre pas. Le vivier reste l'export Salesforce,
  l'API gov et les recherches ciblées.
- **LinkedIn reste inaccessible** (mur de connexion) : les colonnes
  `LinkedIn_Entreprise_URL` et `Lead_Decideur_LinkedIn_URL` ne sont remplies que
  si l'entrée les fournit. L'étape 8 de `scp-lead-pipeline` (WebSearch) reste manuelle.
- **Un LLM peut inventer.** Le prompt interdit toute déduction et exige une
  citation pour chaque signal, mais un chaud doit rester vérifié à la main avant
  d'être appelé — c'est déjà la règle de l'étape 7 du pipeline.
- **Pas de vérification d'état administratif** : ce prototype ne fait pas l'appel
  à `recherche-entreprises.api.gouv.fr`. Passez la colonne `etat_administratif`
  en entrée si vous l'avez, le scoring l'utilisera.
- **Sites en JavaScript lourd** : playwright rend la page, mais certains sites
  (protections anti-bot, cookie walls) renvoient une page vide. Ces prospects
  ressortent en « à confirmer », pas silencieusement perdus.
- Le mode `fixtures` n'appelle aucun LLM : il sert aux tests et aux démonstrations,
  pas à produire un vrai batch.

## Avant de scraper : le cadre

- Ne visiter que des **sites publics d'entreprises**, en B2B, à un rythme poli
  (`--delay`), et respecter un `robots.txt` qui interdit l'accès.
- Les noms et emails de dirigeants collectés sont des **données personnelles**
  (RGPD côté FR, nLPD côté CH) : finalité de prospection B2B, information des
  personnes, droit d'opposition, conservation limitée. Le mode Ollama évite en
  plus de transmettre ces données à un tiers.
- Ne pas committer de spec d'installateur réelle ni d'export Salesforce dans ce
  dépôt : `.gitignore` couvre déjà `batchs-livres/`, `sorties/` et `.env`.

## Tests

```bash
pip install -r requirements-core.txt
python -m pytest          # 51 tests, aucun appel réseau ni LLM
```

Ils couvrent la normalisation Salesforce (téléphones FR/CH, casse française,
codes postaux, picklist Industry), les règles de classement une par une, et la
chaîne complète des fixtures jusqu'aux en-têtes des xlsx.

## Structure

```
scpscrape/schema.py       schéma Pydantic + prompt d'extraction
scpscrape/extract.py      ScrapeGraphAI (réel) et fixtures (hors ligne)
scpscrape/spec.py         lecture de la spec YAML de l'installateur
scpscrape/scoring.py      exclusions, hors-cible, à confirmer, scoring
scpscrape/salesforce.py   normalisation aux formats de l'org SCP
scpscrape/pipeline.py     assemblage et ventilation dans les 4 paniers
scpscrape/export.py       écriture des xlsx (+ versionnage _v2 si le fichier existe)
scpscrape/cli.py          ligne de commande et récapitulatif
config/                   spec d'exemple
fixtures/                 8 prospects et leurs extractions enregistrées
tests/                    51 tests hors ligne
```
