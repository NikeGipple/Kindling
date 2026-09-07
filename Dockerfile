# Immagine unica per il bot di ingestion (bot/), il job di calcolo del grafo
# (job/) e l'API di sola lettura (api/). Usata sia per lo sviluppo locale via
# docker-compose sia, invariata, per il deploy sulla droplet DigitalOcean —
# vedi docs/architettura/architettura.md.
#
# Un'immagine sola e non tre: il job importa python-igraph, che bot e api non
# caricano mai, quindi il costo a runtime sugli altri due e' nullo e resta un
# solo artefatto da costruire e tenere allineato sulla droplet. requirements.txt
# include gia' fastapi/uvicorn/pydantic per lo stesso motivo.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY job/ ./job/
COPY api/ ./api/

# Versione del codice INCISA nell'immagine, non letta da .env a runtime: il
# job scrive questo valore in graph_snapshots.code_version e metric_runs.code_version
# per rispondere a "quale codice ha prodotto questi numeri", e la risposta e' il
# codice CHE STA IN QUESTA IMMAGINE, non quello dell'ultimo `git pull` sulla
# droplet — che puo' essere diverso, se qualcuno ha fatto pull senza rebuild.
# Un valore letto da .env potrebbe mentire con l'aria di essere compilato.
#
# ARG e non solo ENV: senza il build-arg il default e' stringa vuota, e
# _code_version() (job/main.py) la tratta come assente — nessun errore di
# build, solo un valore NULL come e' sempre stato finora, cioe' un fallback
# sicuro invece di un build rotto.
#
# Dopo i COPY e subito prima di CMD, non prima: il valore cambia a ogni
# deploy (e' l'hash del commit), quindi mettere ARG/ENV qui invalida solo
# questo layer e CMD, mai la RUN pip install ne' i tre COPY sopra — che restano
# nella cache anche quando cambia solo il commit.
ARG KINDLING_CODE_VERSION=
ENV KINDLING_CODE_VERSION=$KINDLING_CODE_VERSION

CMD ["python", "-m", "bot.main"]
