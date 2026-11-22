# Environments, Releases & Operations

## Environments
| | Development | Staging | Production |
|---|---|---|---|
| Where | Developer laptop | Dedicated machine/VM (or a spare branch box) | Each restaurant's branch server; the organisation's cloud |
| How | INSTALL.md §C (plain processes, hot reload) | `docker compose` with `deploy/env/staging.env.example` | `docker compose` with `deploy/env/production.env.example` |
| Data | Demo seed / generated | Anonymised copy or demo; **never production data or the production cloud** | Real |
| Secrets | Dev defaults (refused when `APP_ENV=production`) | Own secrets | Own secrets in `secrets/` (Docker secrets) and `.env`; never in git |
| Who deploys | Anyone | CI (release workflow) | CI after **manual approval** in the GitHub "production" environment |

The app refuses to start in production with a default or weak `APP_JWT_SECRET` (or, in the cloud, a weak `APP_CLOUD_MASTER_KEY`).

## Secure configuration
* `.env` holds non-secret settings plus the JWT and metrics tokens (file mode 600). The database password, the `pgpass` used by backups and the **backup passphrase** are Docker secrets under `secrets/` (git-ignored).
* Rotate `APP_JWT_SECRET` to sign everyone out. Rotating the database password needs the same value in `db_password.txt` and `pgpass`.
* Production hides `/api/docs` and the public health check shows no sync internals. Metrics need `APP_METRICS_TOKEN`.

## Release versioning
Semantic versioning in `VERSION` (currently **0.4.1**), reported by `/api/v1/health/live` and baked into
the image (`APP_APP_VERSION`, OCI label). Database schema versions are Alembic revisions (`0004perf`),
carried on every sync event. `CHANGELOG.md` lists every release.

## CI/CD (`.github/workflows/`)
* **ci.yml**, on every push and PR:
  * dependency CVE audit (`pip-audit`, `npm audit`) — fails on any known vulnerability;
  * migrations vs models (`alembic check`);
  * all backend tests on real PostgreSQL (unit, API, sync with a real cloud server, security, integrity, recovery, migrations);
  * frontend type check, unit tests and build;
  * build of both Docker images, plus a smoke test that the app image starts and reports its version.
* **release.yml**, on tag `vX.Y.Z` (must equal `VERSION`):
  1. re-runs CI;
  2. publishes `ghcr.io/<repo>:X.Y.Z`;
  3. deploys to **staging** with `scripts/deploy/deploy.sh` and runs smoke tests;
  4. **production** waits for a reviewer's approval, then deploys the same image.

  Nothing reaches production without passing staging.

## Deploying (what `scripts/deploy/deploy.sh` does)
1. Records the current schema revision; takes a **pre-deploy backup and verifies it by restoring it**.
2. Pulls the release image and restarts `app` and `sync`. Migrations run on start, each in its own transaction, so a failing migration leaves the previous revision intact (tested).
3. Waits up to 3 minutes for `/health/live` to report the new version **and** `/health/ready` to return 200.
4. On failure: stops the new version, **restores the pre-deploy backup if the schema changed**, and starts the previous version.

**Cloud first:** upgrade the cloud before branches. The cloud accepts the previous and current schema versions; events from newer branches wait (never dropped) until the cloud is upgraded.

### Database migration process
* Migrations are written with Alembic, reviewed, and must pass `alembic check`, the downgrade/upgrade test and the failing-migration test in CI.
* Each migration runs in its own transaction (`transaction_per_migration`).
* Data back-fills on append-only tables (e.g. `0003audit`) disable the protective trigger only inside that migration's transaction.
* Rollback: automatic via deploy.sh. Manually: `alembic downgrade <rev>`, or restore the pre-deploy backup.

## Monitoring & alerting
* `/api/v1/health/live` (process), `/api/v1/health/ready` (database + disk); Docker health checks use them.
* `/api/v1/metrics` (Prometheus format, bearer `APP_METRICS_TOKEN`):
  * database up, disk free, last backup age;
  * sync queue by status, oldest unsent, halted, last cloud success;
  * documents not yet in the cloud, low-stock count;
  * per-branch halted, last seen and apply errors (cloud).
* `deploy/monitoring/alerts.yml`: Prometheus rules for database down, instance down, disk < 10% / < 3%, no backup in 26 h, failed restore test, sync rejected, sync halted, backlog older than 24 h, branch not seen for 6 h.
* No Prometheus? `scripts/ops/healthcheck.sh` from cron every 5 minutes posts problems to a webhook (Slack/Teams/ntfy/email gateway).
* Logs: JSON lines with request id, method, path, status and duration. Errors are logged server-side with a reference id; users see only that reference (no stack traces).
