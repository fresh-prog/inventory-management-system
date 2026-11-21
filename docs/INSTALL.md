# Installation & Operations Guide

There are two ways to run it: **A. Branch server (Docker)** for a restaurant, or **B. Developer setup**.

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
```bash
docker compose up -d --build
```
Open `https://<server-ip>/` from a phone, tablet or PC on the same Wi-Fi. On the first visit:
1. The browser warns about the certificate. Caddy uses a private certificate for the LAN. To stop the warning, install
   Caddy's root certificate on each device once. Copy it from the server with:
   `docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./restaurant-root.crt`
2. The **setup screen** appears (only while no user exists). Enter the branch, currency (UGX: 0 decimals; ERN: 2) and the owner account.
3. As owner, open **Settings → Users & roles** and create staff accounts (manager, purchaser, kitchen, viewer).

Tablets and phones can "Add to Home Screen" to install the app (PWA).

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

## B. Developer setup (tested)

Requirements: Python 3.12+, Node 22+, PostgreSQL 16.

```bash
createdb restaurant && createdb restaurant_test
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

Configuration (environment variables, prefix `APP_`): `APP_DATABASE_URL`, `APP_JWT_SECRET` (required in production, 32+ chars),
`APP_ACCESS_TOKEN_MINUTES`, `APP_FRONTEND_DIST` (serve the built PWA from the API), `APP_ENV`.

### Running the tests
```bash
cd backend && .venv/bin/python -m pytest -q
```
(uses the `restaurant_test` database; override with `APP_DATABASE_URL`)
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
