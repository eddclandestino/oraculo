# ORÁCULO — Production Dockerfile
# Multi-stage build for smaller final image.

# ── Stage 1: Install dependencies ──
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies needed for numpy/yfinance compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Final slim image ──
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Port exposed by the app (Cloud Run uses $PORT but defaults to 8080)
EXPOSE 8080

# Health check for Cloud Run (optional but good practice)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()" || exit 1

# Run with single worker (WebSockets are stateful, no multi-worker)
# --timeout-keep-alive 300: keep idle connections alive for 5 min
# --ws-max-size 16MB: allow large WebSocket messages (video frames)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--timeout-keep-alive", "300", \
     "--ws-max-size", "16777216"]
