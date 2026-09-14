# Immagine unica per il bot di ingestion (bot/), il job di calcolo del grafo
# (job/), l'API di sola lettura (api/) e la dashboard (dashboard/). Usata sia
# per lo sviluppo locale via docker-compose sia, invariata, per il deploy sulla
# droplet DigitalOcean — vedi docs/architettura/architettura.md.
#
# Un'immagine sola e non quattro: il job importa python-igraph, che bot, api e
# dashboard non caricano mai, quindi il costo a runtime sugli altri e' nullo e resta un
# solo artefatto da costruire e tenere allineato sulla droplet. requirements.txt
# include gia' fastapi/uvicorn/pydantic per lo stesso motivo.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY job/ ./job/
COPY api/ ./api/
# dashboard/ importa api/models.py per validare le risposte dell'API: senza la
# riga sopra non partirebbe. Aggiunta nello stesso commit della cartella, non al
# primo deploy (CLAUDE.md, errore n. 5). I template stanno dentro dashboard/ e
# arrivano con questa riga.
COPY dashboard/ ./dashboard/

# tools/ NON va copiata qui, ed e' l'inverso esatto dell'errore n. 5 di
# CLAUDE.md: li' api/ andava aggiunta ai COPY e nessuno lo fece. Qui
# tools/fixture_api.py e' un attrezzo di sviluppo — il server di dati
# sintetici contro cui si costruisce la dashboard — che non gira mai in un
# container. Aggiungerlo "per coerenza" con le tre righe qui sopra sarebbe
# l'errore, non la correzione.

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
