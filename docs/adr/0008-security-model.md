# ADR-0008: Security model

* **Passwords:** Argon2id (argon2-cffi), per-user salt; login rate-limited and locked out progressively.
* **Sessions:** short-lived access token (15 min) plus a rotating refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie; refresh tokens are stored hashed and revocable. Staff on shared tablets can use a PIN quick-switch inside a device session.
* **MFA (TOTP)** is required for owner/admin roles and for **any** access through the cloud (remote).
* **RBAC:** roles (Owner, Manager, Purchaser, Kitchen, Accountant, Viewer) map to fine-grained permissions (e.g. `purchase.confirm`, `payment.post`, `stock.adjust`, `stock.override`, `recipe.publish`). Checks run on the API, never only in the UI.
* **Branch isolation:** every query is scoped by `branch_id` taken from the session, never from the request body. The cloud DB adds PostgreSQL Row-Level Security as defence in depth.
* **Uploads:** size limit, MIME sniffing plus an allow-list (JPEG/PNG/WEBP/PDF), re-encoding of images, random storage names, served only through an authorised endpoint.
* **Secrets:** environment or Docker secrets, never in the repo or frontend; the frontend only ever talks to the API.
* **Transport:** HTTPS everywhere (Caddy on the LAN, managed TLS in the cloud); branch↔cloud sync uses mTLS with per-branch client certificates.
* **Audit:** append-only `audit_log` (who, what, when, device, before/after) for purchases, payments, adjustments, counts, recipe publishing, role and settings changes. The DB role used by the app has no UPDATE or DELETE on the audit table.
* **Backups:** encrypted before they leave the server.
