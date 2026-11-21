# ADR-0003: Offline-first — branch server is the system of record

**Context.** Normal operations must never require the cloud. Remote owners still need visibility. Financial and stock records must not use last-write-wins.

**Decision.**
1. Each branch server owns its branch's transactional data (purchases, payments entered there, batches, movements). Each record carries `branch_id`, `origin_device_id`, a UUIDv7 `id` and an `idempotency_key`.
2. Transactional records are **append-only events** (confirm, void, reversal), never updated in place. A correction is a new reversing record. A conflict between two branches is therefore impossible by construction, because only one branch owns a record.
3. **Outbox** (branch): written in the same transaction as the business change. A publisher sends events in `branch_sequence` order. An event is marked `acked` only after the cloud returns a signed acknowledgement containing the event ID. Rows are never deleted before an ack and are retained for 90 days afterwards.
4. **Inbox** (cloud and branch): a unique `(source, event_id)` constraint makes re-delivery harmless.
5. **Master data** (ingredients, recipes, suppliers) can be edited on either side. Rows carry a `version`. An update sends `base_version`; on a mismatch the change goes to a **conflict review queue** rather than overwriting. Field-level auto-merge applies only to non-financial descriptive fields (description, notes).
6. **Payments entered remotely (cloud)** for a branch invoice are forwarded to the branch as *requests* and posted by the branch. Any over-allocation is rejected there, so one authority validates every invoice balance.
7. Devices (PWA) talk only to their branch server. Offline from the branch server, a device keeps **drafts** in IndexedDB and a command queue with idempotency keys. Commands are posted when the device reconnects. FIFO and balance checks always run on the server.
8. Remote views show `as of <last acknowledged sync time>` and a count of unsynchronised events.

**Consequences.** Simple, auditable sync with no CRDTs. Cross-branch stock transfers (future) will be modelled as paired transfer-out and transfer-in events with in-transit stock.
