# Small official Python image. "slim" has no extra packages I do not need.
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: no .pyc files inside the container
# PYTHONUNBUFFERED: logs show up immediately in `docker compose logs`
# PIP_NO_CACHE_DIR: keeps the image smaller
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# I copy and install requirements BEFORE copying the source code.
# Docker caches this layer, so when I only change my code, the image
# rebuild skips the slow pip install step.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY sql/ ./sql/

# My package lives in src/, this makes "import pipeline" work.
ENV PYTHONPATH=/app/src

# I create a normal user and switch to it. Running containers as root
# is a known bad practice, especially with sensitive clinical data.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT []
CMD ["python", "-m", "pipeline", "--help"]
