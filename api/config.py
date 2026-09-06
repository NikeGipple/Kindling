"""Configurazione dell'API, tutta in un posto solo.

Nessuna credenziale qui dentro: la connection string arriva da
``API_DATABASE_URL`` nel .env, che non e' in git. E' una variabile distinta da
``DATABASE_URL``: quella e' del ruolo proprietario usato da bot e job, questa
del ruolo di sola lettura (docs/architettura/api.md 4). Confonderle vanificherebbe
la garanzia — l'API girerebbe con i permessi di scrittura di chi le tabelle le
crea.
"""

from __future__ import annotations

import os

# Serie storiche brevi: il dashboard mostra qualche mese, non tutto lo storico.
# Il massimo esiste perche' un limit non vincolato e' un modo di chiedere al
# database di scaricare in memoria tutto quello che ha, su una macchina da 1 GB.
DEFAULT_LIMIT = 12
MAX_LIMIT = 200

# Le sole tabelle che l'API puo' leggere. Non e' un elenco da mantenere: e'
# l'applicazione del criterio di api.md 1 alle tabelle che esistono oggi, e il
# database la impone comunque (migration 0010). Vive qui perche' un lettore del
# codice veda subito il perimetro.
READABLE_TABLES = (
    "guilds",
    "metric_runs",
    "metric_robustness",
    "metric_communities",
    "metric_community_sizes",
    "metric_cohorts",
    "metric_cohort_retention",
)


def database_url() -> str:
    url = os.environ.get("API_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "API_DATABASE_URL non impostata. E' la connection string del ruolo di "
            "sola lettura kindling_api, distinta da DATABASE_URL. Vedi .env.example."
        )
    return url
