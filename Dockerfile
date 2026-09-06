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

CMD ["python", "-m", "bot.main"]
