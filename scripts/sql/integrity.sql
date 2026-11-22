-- Business-integrity checks in plain SQL (independent of the application code).
-- Every row returned is a violation. Used by verify_restore.sh and the nightly check.
\set ON_ERROR_STOP 1
SELECT 'batch remaining != original - allocations' AS violation, b.id::text AS ref
FROM stock_batch b LEFT JOIN (SELECT batch_id, sum(quantity) q FROM stock_allocation GROUP BY batch_id) a ON a.batch_id = b.id
WHERE b.remaining_quantity <> b.original_quantity - COALESCE(a.q, 0)
UNION ALL
SELECT 'negative or over-full batch', id::text FROM stock_batch WHERE remaining_quantity < 0 OR remaining_quantity > original_quantity
UNION ALL
SELECT 'movement without source', id::text FROM stock_movement WHERE source_id IS NULL OR source_type IS NULL
UNION ALL
SELECT 'purchase line without exactly one batch', pl.id::text FROM purchase_line pl
LEFT JOIN stock_batch b ON b.purchase_line_id = pl.id GROUP BY pl.id, pl.base_quantity
HAVING count(b.id) <> 1 OR min(b.original_quantity) <> pl.base_quantity
UNION ALL
SELECT 'supplier ledger != outstanding invoices', s.id::text FROM supplier s
LEFT JOIN (SELECT supplier_id, sum(amount) l FROM supplier_ledger_entry GROUP BY supplier_id) le ON le.supplier_id = s.id
LEFT JOIN (
  SELECT i.supplier_id, sum(CASE WHEN i.status = 'voided' THEN 0 ELSE i.total - COALESCE(p.paid, 0) - COALESCE(c.cr, 0) END) o
  FROM purchase_invoice i
  LEFT JOIN (SELECT a.invoice_id, sum(a.amount) paid FROM payment_allocation a JOIN supplier_payment sp ON sp.id = a.payment_id
             WHERE sp.status = 'confirmed' GROUP BY a.invoice_id) p ON p.invoice_id = i.id
  LEFT JOIN (SELECT invoice_id, sum(amount) cr FROM supplier_credit WHERE status = 'confirmed' GROUP BY invoice_id) c ON c.invoice_id = i.id
  GROUP BY i.supplier_id) ob ON ob.supplier_id = s.id
WHERE COALESCE(le.l, 0) <> COALESCE(ob.o, 0)
UNION ALL
SELECT 'invoice over-paid', i.id::text FROM purchase_invoice i JOIN payment_allocation a ON a.invoice_id = i.id
JOIN supplier_payment sp ON sp.id = a.payment_id AND sp.status = 'confirmed' WHERE i.status = 'confirmed'
GROUP BY i.id, i.total HAVING sum(a.amount) > i.total
UNION ALL
SELECT 'payment allocations != payment amount', sp.id::text FROM supplier_payment sp JOIN payment_allocation a ON a.payment_id = sp.id
GROUP BY sp.id, sp.amount HAVING sum(a.amount) <> sp.amount
UNION ALL
SELECT 'more than one published recipe version', recipe_id::text FROM recipe_version WHERE status = 'published'
GROUP BY recipe_id HAVING count(*) > 1;
