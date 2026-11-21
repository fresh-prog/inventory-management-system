# 06 — Acceptance Tests

Given/When/Then scenarios. **[auto]** = automated today (`backend/tests`). Others are written
now and automated in the stage shown.

## Costing (current basis)
* **AT-C1 [auto]** Given cost per portion 4.50 and target 30 %, the recommended price is 15.00.
* **AT-C2 [auto]** Given 5 kg beef @ 18/kg + 1 kg onion @ 2/kg for 20 portions, selling at 15: batch 92, portion 4.60, food cost 30.67 %, contribution 10.40, flagged over target.
* **AT-C3 [auto]** A NET line of 800 g onion with 20 % trim issues 1000 g. A GROSS 1000 g line with 20 % loss still issues 1000 g (no double loss).
* **AT-C4 [auto]** Converting kg → l without a density rule is refused. With olive oil 1 l = 910 g, 2 l = 1.82 kg.
* **AT-C5 (S3)** Confirming a purchase that raises beef's cost creates new cost snapshots for every published recipe using beef; old snapshots stay unchanged; recipes now over target show on the dashboard.

## Purchasing & payables
* **AT-P1 [auto]** Line and invoice arithmetic with discounts and tax validates; mismatches beyond tolerance are reported.
* **AT-P2 (S4)** Confirming a 3-line invoice creates exactly 1 invoice, 3 lines, 3 batches, 3 receipt movements, 3 price-history rows, 1 ledger entry, the audit rows and the outbox events, in one transaction.
* **AT-P3 (S4)** If a failure is injected after the batches are written but before commit, **no** rows exist afterwards.
* **AT-P4 (S4)** Submitting the same confirm twice (same Idempotency-Key) yields one purchase; the second response is a replay.
* **AT-P5 (S4)** Confirming a second invoice with the same supplier and reference is rejected.
* **AT-P6 [auto]** Invoice 100, posted payment 30, draft payment 50, posted credit 10 → outstanding 60, partially paid.
* **AT-P7 [auto]** With 90 posted against 100, allocating 10.01 is rejected; 10 is accepted.
* **AT-P8 (S4)** Two users paying the last 50 of the same invoice simultaneously → exactly one succeeds.
* **AT-P9 (S7)** An OCR'd invoice is never visible in stock or balances until confirmed; the original image is linked to the purchase.

## FIFO inventory
* **AT-F1 [auto]** A 10@9, B 10@11, C 10@12; consume 15 → A10 = 90, B5 = 55, total 145; remaining B5@11, C10@12; value 175.
* **AT-F2 [auto]** Consuming 31 with 30 available is blocked by default; with the override policy, 30 are allocated (cost 320) and a shortfall of 2 is recorded.
* **AT-F3 [auto]** Batches received at the same timestamp are allocated by sequence.
* **AT-F4 [auto]** Quarantined, empty and other-branch batches are skipped.
* **AT-F5 (S5)** 20 concurrent consumers of 1 kg against 10 kg in stock → exactly 10 succeed, remaining = 0, never negative.
* **AT-F6 (S5)** A production run of 50 portions deducts its ingredients once; recording the sale of 50 portions (future POS) does not deduct ingredients again.
* **AT-F7 (S5)** Reversing a consumption restores the same batches at the same costs; history shows both movements.
* **AT-F8 (S5)** Spoilage without a reason, or by a user without `stock.adjust`, is rejected.
* **AT-F9 (S5)** A stock count with a variance posts an adjustment only after approval.

## Sync & offline
* **AT-S1 (S8)** With the internet down, purchases, consumption and payments work on the branch; the outbox grows; after reconnecting, everything syncs exactly once.
* **AT-S2 (S8)** Killing the publisher after send but before the ack is stored → resend → the cloud inbox dedupes it; the event is acked once.
* **AT-S3 (S6)** A tablet offline from the branch server saves a purchase draft; the UI says "Saved on this device — not yet posted"; it shows no success toast until the server confirms.
* **AT-S4 (S8)** Two sides edit the same ingredient name from version 3 → a conflict appears in the review queue; nothing is overwritten.

## Security
* **AT-X1 (S3)** A user of branch A cannot read or post branch B data, even by passing B's IDs.
* **AT-X2 (S3)** Remote (cloud) login for an Owner without MFA is refused.
* **AT-X3 (S4)** Uploading an executable renamed `.jpg` is rejected.
