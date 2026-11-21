# ADR-0002: Technology stack evaluation

| Layer | Choice | Why | Alternatives considered |
|---|---|---|---|
| API | Python 3.12+, FastAPI, Pydantic v2 | Typed request validation, OpenAPI contracts generated from code, strong `Decimal` support, team familiarity | Django (heavier ORM coupling; also a good option), Node/NestJS (weaker decimal story) |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Explicit transactions, `with_for_update()`, mature migrations | Django ORM, raw SQL |
| DB | PostgreSQL 16 (branch and cloud) | `NUMERIC`, row locks, partial indexes, JSONB for OCR payloads, logical replication if needed later | SQLite on the branch (rejected: weak concurrency for 25 users, no row-level locking) |
| Worker | In-process async worker polling the outbox with `FOR UPDATE SKIP LOCKED` | Needs no Redis or broker on branch hardware | Celery+Redis (an extra moving part offline) |
| Frontend | React 18, TypeScript, Vite, TanStack Query, Dexie (IndexedDB), PWA | Requested; mature offline tooling | Flutter (no PWA parity), Vue |
| Edge | Caddy (automatic HTTPS; internal CA on the LAN) | One binary, simple TLS | Nginx + certbot |
| Packaging | Docker Compose per branch; the same images in the cloud | Repeatable installs and upgrades | Native installers |
| OCR | Adapter interface; Tesseract (local) by default, optional cloud engine | Works offline | Cloud-only (fails offline) |

**Verdict:** the requested stack fits. Two refinements: (1) no Redis/Celery on branch servers; (2) Caddy for TLS on the LAN.
