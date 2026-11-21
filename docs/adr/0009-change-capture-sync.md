# ADR-0009: Change-capture outbox, per-branch replica schemas and command-based remote management

**Status:** Accepted (Stage 3). Refines ADR-0003; supersedes its §5 ("master data editable on either side with field-level merge").

**Context.** Stage 3 must sync offline branches to a central cloud with no loss, no duplication and no
last-write-wins for financial or stock data, while keeping branch data isolated and allowing remote management.

**Decision.**
1. **Change capture into a transactional outbox.** ORM mapper hooks record every mutation of a replicated table. At commit, one outbox record with final row images is written *in the same transaction*. Hand-written per-event payloads were rejected: they are easy to get wrong and tend to drift from the schema.
2. **Commit-ordered sequence:** `pg_advisory_xact_lock` is taken immediately before the outbox insert and released at COMMIT. This avoids out-of-order delivery between concurrent writers, at the cost of briefly serialising commits (acceptable at restaurant scale).
3. **Single writer per row:** the branch writes, and the cloud only applies images in sequence order into a replica. No merge logic exists, so no merge bugs exist.
4. **One PostgreSQL schema per branch in the cloud**, created with the branch migrations. Reporting code runs unchanged against a replica (search_path). This gives strong isolation, and one branch can be dropped or restored without touching others.
5. **Remote management = commands executed by the branch** with optimistic version checks and the branch's own validation and locks. Results return in the same event as the resulting changes.
6. **HMAC-signed requests and signed acks** with derived per-branch secrets (no secret storage in the cloud). mTLS remains a possible later hardening step.
7. **Snapshots** for bootstrap and recovery; **high-water reconciliation** for cloud restores; **sequence-regression halt** for branch restores.

**Consequences.**
+ Exact convergence is testable: the tests compare every replicated row byte-for-byte.
+ The cloud can be rebuilt from branches (snapshot), and a branch can be rebuilt from the cloud (restore-from-cloud).
− Every schema change must be applied to the cloud first; KNOWN_SCHEMA_VERSIONS must be maintained.
− Bulk SQL writes bypass capture. Rule: business writes go through the ORM (enforced by review; the convergence tests catch violations).
− One outbox lock per branch serialises commits; measured impact is negligible at restaurant volumes, but it is a known scaling limit.
