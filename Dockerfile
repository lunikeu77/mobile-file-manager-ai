# Stage 1: Build React + Vite + TypeScript + Tailwind SPA
FROM node:20-slim AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: Python 3.11 + FFmpeg + Ghostscript runtime
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    STATIC_DIR=/app/static \
    MAX_CONCURRENT_JOBS=1

# Install native media/PDF engines (ffmpeg includes ffprobe, ghostscript for PDF optimization)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend-builder /build/frontend/dist ./static/

RUN mkdir -p /app/data/workspace /app/data/outputs /app/data/temp

EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
