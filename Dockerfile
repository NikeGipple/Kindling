# Immagine per il bot di ingestion (bot/). Usata sia per lo sviluppo locale
# via docker-compose sia, invariata, per il deploy sulla droplet DigitalOcean
# (Fase 1: solo postgres+bot) — vedi docs/architettura/stack-tecnologico-mvp.md.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

CMD ["python", "-m", "bot.main"]
