# Immagine per il bot di ingestion (bot/). Solo per sviluppo locale via
# docker-compose: l'hosting di riferimento (Fly.io/Railway) è descritto in
# architettura/stack-tecnologico-mvp.md.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

CMD ["python", "-m", "bot.main"]
