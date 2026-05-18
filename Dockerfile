# ─────────────────────────────────────────────────────────────────
#  Media Request Firewall — Dockerfile
#  Works identically on Windows (Docker Desktop) and Linux.
# ─────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Keeps Python from buffering stdout/stderr (you see logs in real time)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install dependencies first (layer cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Web UI runs on 7878  (chosen to avoid conflicts with common media apps)
EXPOSE 7878

# Default: start the web UI + background firewall loop
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7878", "--reload"]
