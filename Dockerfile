# ── build the PWA ──
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── API + static files ──
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_FRONTEND_DIST=/app/web
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && apt-get clean \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY scripts/ ./scripts/
COPY --from=web /web/dist ./web
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/v1/health/live')"
# Migrations run on start (forward-only). Take a backup before upgrading — see docs/INSTALL.md.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers"]
