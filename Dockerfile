
# --- builder ---
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kuching

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Utilities for healthcheck (pgrep)
USER root
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*

# Copy source
COPY src ./src

# Safer user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Defaults (overridable by .env / compose)
ENV STRATEGY=moderate MODE=paper CAPITAL=200 LOG_LEVEL=INFO

# Healthcheck: fail if engine process not found
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD sh -c "pgrep -f 'src.engine' >/dev/null || exit 1"

# Run forever (engine loop must not exit)
CMD ["python", "-m", "src.engine", "--strategy", "moderate", "--mode", "paper", "--capital", "200"]
