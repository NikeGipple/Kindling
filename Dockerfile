# Immagine unica per il bot di ingestion (bot/) e per il job di calcolo del
# grafo (job/). Usata sia per lo sviluppo locale via docker-compose sia,
# invariata, per il deploy sulla droplet DigitalOcean — vedi
# docs/architettura/architettura.md.
#
# Un'immagine sola e non due: il job importa python-igraph, che il bot non
# carica mai, quindi il costo a runtime sul bot e' nullo e resta un solo
# artefatto da costruire e tenere allineato sulla droplet.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY job/ ./job/

CMD ["python", "-m", "bot.main"]
