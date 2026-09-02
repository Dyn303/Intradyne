
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

# Copy source. The dashboard ships inside it, at
# src/intradyne/api/static, so there is no separate frontend build.
COPY src ./src

# The research record. Without these the Research panel renders every
# entry as "not generated", which looks like the tests were never run
# rather than like the files were never copied. Only what the repo
# tracks is baked in; locally generated artifacts arrive by volume
# mount, so the image stays reproducible.
COPY docs/universe_candidates.json docs/universe_timeline.json ./docs/
COPY artifacts/production_params.json ./artifacts/

# The database lives here on a named volume. The directory must exist in
# the image and be owned by appuser first: Docker initialises a fresh
# named volume from whatever is at that path in the image, ownership
# included, and a missing path yields a root-owned volume the app cannot
# write to.
RUN mkdir -p /app/state /app/data

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
