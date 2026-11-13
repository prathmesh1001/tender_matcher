# API image: FastAPI, CPU PyTorch for embeddings and reranking, Tesseract for scanned pages.
FROM python:3.14.4-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so editing code does not reinstall them. The CPU build of PyTorch keeps the
# image gigabytes smaller than the CUDA default; pip then accepts it for sentence-transformers.
COPY pyproject.toml constraints.txt ./
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -c constraints.txt .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY config ./config
COPY ontology ./ontology

RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/.cache /data /hf-cache \
    && chown -R app:app /app/.cache /data /hf-cache
USER app

EXPOSE 8000

# The API; docker compose runs the same image as `python -m app.workers.worker` for the runs.
# One uvicorn worker: each API process queues a catalogue warm-up when it starts.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1"]
