# Installation & Operations Guide

Sections: **A. Branch server** (one per restaurant, works offline) · **B. Cloud** (optional, one per organisation) · **C. Developer setup**.

> The Docker files were written and syntax-checked, but **the image build was not executed in this
> development environment (no Docker daemon was available)**. The developer setup below *was* run and
> fully tested. Please run section A once on the target machine and report any issue.

## A. Branch server (Docker Compose)

**Hardware:** any 64-bit mini-PC/laptop with 4 GB+ RAM (8 GB recommended), an SSD, and a UPS.
Connect it to the restaurant router with a cable and give it a fixed LAN IP address.

```bash
git clone https://github.com/fresh-prog/inventory-management-system.git
```
```bash
cd inventory-management-system
```
```bash
cp .env.example .env && openssl rand -hex 32
```
Paste the generated value into `.env` as `APP_JWT_SECRET`. Then create the database password secret:
```bash
openssl rand -base64 24 | tr -d '\n' > secrets/db_password.txt && chmod 600 secrets/db_password.txt
```
Backups (scheduled by the `backup` service) need two more secrets: a pgpass line for the database and the
**backup passphrase**. Store a copy of the passphrase OFF the server: without it, backups cannot be restored.
```bash
printf 'db:5432:restaurant:app:%s\n' "$(cat secrets/db_password.txt)" > secrets/pgpass && chmod 600 secrets/pgpass
```
```bash
openssl rand -base64 32 > secrets/backup_passphrase.txt && chmod 600 secrets/backup_passphrase.txt
```
Mount a USB disk or network share for off-site copies and set `OFFSITE_PATH` in `.env` (see DISASTER-RECOVERY.md).
Set `APP_METRICS_TOKEN` in `.env` to enable monitoring (DEPLOYMENT.md).
```bash
docker compose up -d --build
```
Open `https://<server-ip>/` from a phone, tablet or PC on the same Wi-Fi. On the first visit:
1. The browser warns about the certificate. Caddy uses a private certificate for the LAN. To stop the warning, install
   Caddy's root certificate on each device once. Copy it from the server with:
   `docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./restaurant-root.crt`
2. The **setup screen** appears (only while no user exists). Enter the branch, currency (UGX: 0 decimals; ERN: 2) and the owner account.
3. As owner, open **Settings → Users & roles** and create staff accounts (manager, purchaser, kitchen, viewer).

Tablets and phones can "Add to Home Screen" to install the app (PWA). Each device registers itself
at first sign-in; the owner can revoke devices under **Settings → Devices**.

The `sync` service (cloud synchronisation worker) idles until the branch is connected to a cloud (section B).
Receipts are stored in the `files` volume: back it up together with the database.
Keep the server's clock correct (NTP), because cloud requests are time-stamped and signed.

### Backups (do this before go-live)
Run `pg_dump` inside the database container, so the client and server versions always match:
```bash
mkdir -p backups && docker compose exec -T db pg_dump -U app -Fc restaurant > "backups/restaurant-$(date +%F).dump"
```
Encrypt it before copying it off the machine (keep the passphrase file somewhere other than the server):
```bash
openssl enc -aes-256-cbc -pbkdf2 -salt -in "backups/restaurant-$(date +%F).dump" -out "backups/restaurant-$(date +%F).dump.enc" -pass file:/root/backup-passphrase
```
* Schedule both nightly (cron on the host). Copy the `.enc` files to a USB drive or cloud storage.
* Outside Docker, `scripts/backup.sh` does the same (dump, verify, checksum, optional encryption, keep 30); it is the script exercised by the automated recovery test.
* **Restore test** (monthly): restore into a *new* database (`scripts/restore.sh <file> restaurant_restore_test`, or `pg_restore` inside the db container), point a test instance at it and open **Reports → Data checks**. All checks must pass.

### Upgrading
1. Take a backup and verify it (above).
2. `git pull && docker compose up -d --build`. Migrations run automatically and are forward-only.
3. Open **Reports → Data checks**.

## B. Cloud (optional: remote reports, off-site copy, remote management)

Run once per organisation, on a server reachable from the internet (DNS name pointing to it):
```bash
cp .env.cloud.example .env.cloud
```
Fill in `.env.cloud`: two different values from `openssl rand -hex 32` for `APP_JWT_SECRET` and `APP_CLOUD_MASTER_KEY`, plus `CLOUD_DOMAIN`.
Keep `APP_CLOUD_MASTER_KEY` safe: every branch's sync key is derived from it.
```bash
openssl rand -base64 24 | tr -d '\n' > secrets/cloud_db_password.txt && chmod 600 secrets/cloud_db_password.txt
```
```bash
docker compose -f docker-compose.cloud.yml up -d --build
```
Create the first cloud administrator (there is no web setup screen in the cloud, by design):
```bash
docker compose -f docker-compose.cloud.yml exec cloud python -m app.cloud.cli create-admin you@example.com "Your Name"
```
Sign in at `https://<CLOUD_DOMAIN>/`. The first sign-in shows a key to add to an authenticator app;
remote access always requires the 6-digit code.

**Connect a branch:** in the cloud, **Add branch** → note the one-time registration code (valid 7 days).
At the restaurant, as owner: **Settings → Cloud sync** → enter `https://<CLOUD_DOMAIN>` and the code → **Connect**.
The branch sends a full copy, then only changes. Its state is visible on both sides.

Upgrading: **always upgrade the cloud before the branches** (see [SYNC-RUNBOOK.md](SYNC-RUNBOOK.md) §4).
Recovery procedures: [SYNC-RUNBOOK.md](SYNC-RUNBOOK.md). Design: [SYNC-ARCHITECTURE.md](SYNC-ARCHITECTURE.md).

## C. Developer setup (tested)

Requirements: Python 3.12+, Node 22+, PostgreSQL 16.

```bash
createdb restaurant && createdb restaurant_test && createdb restaurant_cloud && createdb restaurant_cloud_test
```
```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
```
```bash
.venv/bin/alembic upgrade head
```
```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```
In another terminal:
```bash
cd frontend && npm ci && npm run dev
```
Open http://localhost:5173. To load the demo restaurant (all data goes through the API, so every rule applies):
```bash
backend/.venv/bin/python scripts/seed_demo.py http://localhost:8000
```
Demo sign-in: `owner@demo.restaurant` / `demo-owner-password`. Staff accounts (`chef@`, `buyer@`, `manager@demo.restaurant`) use `demo-staff-password`.

Optional: run a local cloud next to it (port 8100) and the sync worker, then connect them under
**Settings → Cloud sync** with `http://127.0.0.1:8100` and a code from the cloud console:
```bash
APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/restaurant_cloud .venv/bin/python -m app.cloud.cli migrate
```
```bash
APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/restaurant_cloud .venv/bin/python -m app.cloud.cli create-admin you@example.com "You"
```
```bash
APP_DEPLOYMENT_PROFILE=cloud APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/restaurant_cloud .venv/bin/uvicorn app.main:app --port 8100
```
```bash
.venv/bin/python -m app.sync.worker
```

Configuration (environment variables, prefix `APP_`): `APP_DATABASE_URL`, `APP_JWT_SECRET` (required in production, 32+ chars),
`APP_ACCESS_TOKEN_MINUTES`, `APP_FRONTEND_DIST` (serve the built PWA from the API), `APP_ENV`, `APP_FILES_DIR` (receipts),
`APP_DEPLOYMENT_PROFILE` (`branch` | `cloud`), `APP_CLOUD_MASTER_KEY` (cloud only), `APP_SYNC_BATCH_SIZE`, `APP_SYNC_MAX_BACKOFF_SECONDS`,
`APP_OUTBOX_RETENTION_DAYS`.

### Running the tests
```bash
cd backend && .venv/bin/python -m pytest -q
```
(uses the `restaurant_test` and `restaurant_cloud_test` databases; override with `APP_DATABASE_URL` / `APP_CLOUD_TEST_DATABASE_URL`)
```bash
cd frontend && npm run typecheck && npm test
```
End-to-end UI check and screenshots (needs a running server with demo data, and Playwright with Chromium):
```bash
cd frontend && npm run build && node e2e/ui-flows.mjs http://localhost:8000 ../docs/screenshots
```

## Operations
* Health: `GET /api/v1/health/live`, `GET /api/v1/health/ready` (database, disk-space %, pending sync events).
* Logs: JSON lines on stdout with a request id (`docker compose logs app`).
* Integrity: `GET /api/v1/reports/integrity` (also in the UI under **Reports → Data checks**).
