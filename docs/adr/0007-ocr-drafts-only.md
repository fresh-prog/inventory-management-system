# ADR-0007: Pluggable OCR producing reviewable drafts only

* `OcrEngine` interface: `extract(file) -> ExtractedInvoice` (supplier, date, reference, lines, totals, with a confidence score per field).
* Engines: `local_tesseract` (default, works offline on the branch server), `cloud_*` (optional, used only when online and enabled, subject to the data-transfer rules in 07-legal).
* Processing runs as a background job; the UI polls the job status.
* The output is a `purchase_draft` only. Ingredient matching uses supplier-specific aliases first, then fuzzy matching (trigram). Matches below a threshold are shown as "needs confirmation".
* **Nothing reaches stock or balances until a user with `purchase.confirm` reviews the draft and confirms it.** Confirming goes through the same validated "confirm purchase" transaction as a manual entry.
* Confirmed aliases are learned (`supplier_item_alias`) to improve future matches.
* The original file is kept, hashed (SHA-256) and linked to the confirmed purchase. It cannot be deleted while the purchase exists.
