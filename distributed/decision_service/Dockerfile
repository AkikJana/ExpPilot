# Build context is the repo root (see distributed/docker-compose.yml's
# `context: ..`) because the Decision Service imports distributed.* as a
# package — it is not a standalone deployable in isolation from the repo today.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY distributed/ distributed/
COPY shared/ shared/

ENV PORT=8100
EXPOSE 8100

CMD ["sh", "-c", "uvicorn distributed.decision_service.main:app --host 0.0.0.0 --port ${PORT}"]
