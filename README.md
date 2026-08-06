# Salarium Automation

Extraction des statistiques salariales suisses (Q1 / médiane / Q3) issues de
l'Enquête suisse sur la structure des salaires, via l'application Salarium de
l'OFS.

## Architecture

Le moteur interroge directement l'API JSON de l'application (`/api/Calculation/`,
`/api/Data/`) au lieu de piloter un navigateur. Pas de Playwright, pas de
Chromium : un simple client HTTP.

| Fichier | Rôle |
|---|---|
| `app.py` | Interface Streamlit |
| `salarium_scraper.py` | `run_simulations()` — boucle âge / années de service |
| `salarium_api.py` | Client API, mappings libellé → code, modes `--check`, `--validate`, `--run` |
| `salarium_options.py` | Listes d'options de l'interface |
| `check_deploy.py` | Test de santé et de non-régression |

## Installation

```bash
pip install -r requirements.txt
```

## Utilisation

```bash
# test de santé — à lancer avant toute extraction
python check_deploy.py

# extraction par lot depuis un CSV de combinaisons
python salarium_api.py --run combinaisons.csv --out resultats_api.csv

# interface
streamlit run app.py
```

Le CSV de combinaisons attend les colonnes : `identifiant, branche, region,
profession, position, formation, sexe, nationalite, taille, treizieme,
paiements, type_contrat, horaire_hebdo, age_start`.

## Points d'attention

L'API n'est pas documentée publiquement et ne fait l'objet d'aucun engagement de
stabilité. `check_deploy.py` compare trois combinaisons de référence à des
montants connus : si l'OFS renumérote un code, le test échoue avant qu'une
extraction ne produise des données silencieusement fausses.

Le délai entre deux appels est fixé à 0,35 s en séquentiel. Ne pas le réduire ni
paralléliser.

Les données extraites et les fichiers clients sont exclus du dépôt
(voir `.gitignore`).
