
# --- builder ---
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kuching \
    PYTHONPATH=/app/src

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src ./src

# Safer user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Defaults (overridable by .env / compose). Live trading stays off by design:
# see MIGRATION.md phase 5.
ENV MODE=paper \
    LIVE_TRADING_ENABLED=false \
    LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).getcode()==200 else sys.exit(1)"

CMD ["uvicorn", "intradyne.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
