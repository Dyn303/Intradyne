
# --- builder ---
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m venv /opt/venv && . /opt/venv/bin/activate && pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
# --- runtime ---
FROM python:3.11-slim
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# security: add non-root user
RUN useradd -ms /bin/bash appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY . /app
# minimal deps for runtime
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENV HOST=0.0.0.0 PORT=8000
CMD ["uvicorn", "intradyne_lite.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
