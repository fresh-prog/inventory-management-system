# Changelog

## 0.4.0 — Production hardening (Stage 4)
**Critical fixes**
* Change capture is always active wherever business data is written. Previously a process that did not import a web route wrote no sync records.
* Upgraded vulnerable dependencies (PyJWT, Starlette/FastAPI, React Router, Vite, Vitest): 0 known CVEs.
* Backup verification now fails on any failed check. Previously a tampered backup could be reported as verified.

**Security:** numeric/text/list bounds; NUL rejection; per-client rate limits; security headers and sandboxed documents; safe 500/503/507 responses; API docs off in production; minimal public health check; CSV formula-injection protection.

**Integrity:** every stock movement traceable (migration `0003audit`); single inventory-value definition; recipe costs re-computed when conversion rules change.

**Performance (migration `0004perf`):**
* lookup indexes;
* SQL-paginated purchases (≈50×);
* SQL integrity check (30×);
* snapshot-based recipe list (11×);
* lean dashboard;
* fast exact-decimal JSON;
* 4 workers by default;
* gzip + byte-bounded sync batches + multi-row apply (initial copy 7× smaller; backlog 14× smaller);
* incremental rendering for large lists.

**UX:** per-record "saved locally / cloud pending / in cloud ✓" badges; clearer sync indicator; purchases pagination; WCAG fixes (0 axe violations).

**Operations:**
* scheduled encrypted backups with daily restore tests and off-site copy;
* Prometheus metrics, alert rules and a cron health check;
* deploy script with automatic rollback;
* CI audits and image smoke test;
* release pipeline (staging → approved production);
* `VERSION` file.

## 0.3.0 — Hybrid offline/online synchronisation (Stage 3)
## 0.2.0 — MVP (Stage 2)
## 0.1.0 — Foundation (Stage 1)
