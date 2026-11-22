#!/usr/bin/env bash
# Recreate the benchmark database (only ever the one named restaurant_bench).
set -euo pipefail
export PGPASSWORD="${PGPASSWORD:-postgres}"
psql -h localhost -U postgres -qc "DROP DATABASE IF EXISTS restaurant_bench WITH (FORCE)" -c "CREATE DATABASE restaurant_bench OWNER app"
psql -h localhost -U postgres -d restaurant_bench -qc "CREATE EXTENSION pg_stat_statements"
cd "$(dirname "$0")/../../backend"
APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/restaurant_bench .venv/bin/alembic upgrade head >/dev/null
