# Security — controls and test evidence

| Area | Control | Evidence |
|---|---|---|
| Authentication | Argon2id; constant-time responses for unknown users; per-account lockout; **per-client rate limits** on login, setup, cloud login and branch registration | `test_api_auth.py`, `test_login_is_rate_limited_per_client` |
| Sessions | Signed tokens with audience (`branch` / `cloud-access` / `cloud-enroll`), 12 h expiry, server-side revocation (`token_version`: role change, deactivation, password reset, "sign out everywhere") | `test_privilege_escalation_attempts` (forged, `alg=none`, expired, wrong-audience tokens refused) |
| MFA | TOTP mandatory for every cloud (remote) user; enrolment before any access token | `test_cloud_people_api_requires_mfa…` |
| Authorization | 5 roles / 22 permissions checked in the API on every route (not just hidden in the UI); devices registered and revocable; writes require a registered device | `test_every_non_public_route_requires_authentication`, `test_viewer_cannot_change_anything` (every write route), role matrix, escalation tests |
| Branch isolation | Branch from the token, never the request; cross-branch reads and writes return 404/403; the cloud keeps each branch in its own schema; cloud users can be branch-scoped | `test_cross_branch_access_to_files_counts_and_payments`, sync isolation test |
| Injection | Parameterised queries only (SQLAlchemy); schema names derived from UUIDs; probe tests | `test_sql_injection_probes_are_inert` |
| XSS | React escaping; no `dangerouslySetInnerHTML`; CSP `default-src 'self'` with no inline scripts; documents served with `sandbox` CSP and `nosniff` | `test_security_headers_on_api_and_files` |
| CSRF | Not applicable: no cookies are used for auth (bearer header), so browsers cannot attach credentials cross-site | design |
| File uploads | Type detected from content (JPEG/PNG/WEBP/PDF only), 10 MB cap (Caddy 12 MB), random storage names, path-traversal-safe, atomic write, free-space reserve, cleanup on failure | `test_disguised_and_dangerous_uploads_rejected`, chaos disk-full / broken upload |
| Input validation | Numeric bounds per column type, NaN/Inf rejected, text and list length caps, NUL bytes rejected | `test_absurd_numbers…`, `test_oversized_inputs…`, `test_sql_injection…` (NUL) |
| Data exposure | Password hashes never serialised (also excluded from sync); stack traces never returned (reference id instead); API docs off in production; health check minimal | `test_no_password_hashes_or_stack_traces_leak`, `test_production_hides…` |
| CSV export | Formula injection neutralised | `csv.test.ts` |
| Transport | HTTPS via Caddy (LAN CA / public cert), HSTS; branch↔cloud HMAC-signed requests with timestamp, signed acknowledgements and file receipts; gzip bodies capped against decompression bombs | sync tests, `test_compressed_batches_are_size_capped…` |
| Audit | Append-only audit log and history tables enforced by DB triggers | `test_history_tables_are_append_only` |
| Backups | AES-256 encrypted, checksummed, tamper-detected | `test_tampered_backup_is_detected` |
| Dependencies | `pip-audit` + `npm audit` in CI fail on any known CVE; current status **0 known vulnerabilities** | CI |

Open items (see AUDIT.md O2–O5): no external penetration test yet; tokens in `localStorage`; TOTP
secrets and sync keys unencrypted at rest; per-process rate limits.
