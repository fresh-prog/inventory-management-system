import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  BasisTag, Button, Card, Confirm, Empty, ErrorBox, Field, LinkButton, Loading, Page, SearchSelect, StatusBadge, SyncBadge, useToast,
} from "../components/ui";
import { NetworkError, get, newKey, openFile, post, qs, uploadFile } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDraft } from "../lib/hooks";
import { date, dateTime, money, mul, num, qty, smartQty, today, unitCost } from "../lib/format";
import type { Ingredient, Supplier } from "../lib/types";
import { unitsFor, useIngredients } from "./Ingredients";
import { PaymentModal } from "./Suppliers";

export function PurchasesPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = { supplier_id: params.get("supplier_id") || "", payment_status: params.get("payment_status") || "",
                    date_from: params.get("date_from") || "", date_to: params.get("date_to") || "" };
  const suppliers = useQuery({ queryKey: ["suppliers"], queryFn: () => get<Supplier[]>("/suppliers") });
  const PAGE = 50;
  const page = Number(params.get("page") || 0);
  const q = useQuery({ queryKey: ["purchases", filters, page], queryFn: () => get(`/purchases${qs({ ...filters, limit: PAGE, offset: page * PAGE })}`),
                       placeholderData: (prev) => prev });
  const setFilter = (k: string, v: string) => { const p = new URLSearchParams(params); v ? p.set(k, v) : p.delete(k); p.delete("page"); setParams(p); };
  const setPage = (n: number) => { const p = new URLSearchParams(params); n ? p.set("page", String(n)) : p.delete("page"); setParams(p); };
  const rows = q.data?.items || [];
  return (
    <Page title="Purchases" subtitle="Supplier invoices. Confirming one adds the stock, updates prices and the supplier balance in a single step."
      actions={can("purchase.create") && <LinkButton to="/purchases/new">Record purchase</LinkButton>}>
      <Card pad={false}>
        <div className="card-body" style={{ paddingBottom: 0 }}>
          <div className="toolbar">
            <select value={filters.supplier_id} onChange={(e) => setFilter("supplier_id", e.target.value)} aria-label="Supplier">
              <option value="">All suppliers</option>{suppliers.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
            <select value={filters.payment_status} onChange={(e) => setFilter("payment_status", e.target.value)} aria-label="Payment status">
              <option value="">Any payment status</option><option value="unpaid">Unpaid</option><option value="partially_paid">Partially paid</option><option value="paid">Paid</option><option value="voided">Voided</option></select>
            <input type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)} aria-label="From date" style={{ width: "auto" }} />
            <input type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)} aria-label="To date" style={{ width: "auto" }} />
          </div>
        </div>
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : rows.length === 0 ? <Empty title="No purchases found" /> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Date</th><th>Invoice</th><th className="hide-sm">Supplier</th><th className="num">Total</th><th className="num hide-sm">Paid</th><th className="num">Outstanding</th><th>Status</th></tr></thead>
            <tbody>{rows.map((p: any) => (
              <tr key={p.id} className="clickable" onClick={() => nav(`/purchases/${p.id}`)}>
                <td>{date(p.invoice_date)}</td><td><Link to={`/purchases/${p.id}`} onClick={(e) => e.stopPropagation()}>{p.invoice_ref}</Link><div className="muted small">{p.line_count} item(s)</div></td>
                <td className="hide-sm">{p.supplier_name}</td><td className="num">{money(p.total)}</td><td className="num hide-sm">{money(p.amount_paid)}</td>
                <td className="num">{money(p.outstanding)}</td><td><StatusBadge status={p.payment_status} /></td></tr>))}</tbody>
            <tfoot><tr><td colSpan={3} className="hide-sm">This page ({rows.length} of {q.data.total})</td><td className="num">{money(rows.reduce((s: number, r: any) => s + (r.status === "confirmed" ? num(r.total) : 0), 0))}</td>
              <td className="num hide-sm">{money(rows.reduce((s: number, r: any) => s + num(r.amount_paid), 0))}</td><td className="num">{money(rows.reduce((s: number, r: any) => s + num(r.outstanding), 0))}</td><td /></tr></tfoot>
          </table></div>)}
        {q.data && q.data.total > PAGE && (
          <div className="card-body row between">
            <span className="muted small">Page {page + 1} of {Math.ceil(q.data.total / PAGE)} · {q.data.total} invoices</span>
            <span className="row"><Button variant="secondary" className="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button variant="secondary" className="sm" disabled={(page + 1) * PAGE >= q.data.total} onClick={() => setPage(page + 1)}>Next</Button></span>
          </div>)}
      </Card>
    </Page>
  );
}

type PLine = { ingredient_id: string; description: string; quantity: string; unit: string; unit_price: string; line_discount: string; line_total: string };
type PDraft = { key: string; supplier_id: string; invoice_ref: string; supplier_tax_ref: string; invoice_date: string; due_date: string;
  discount_total: string; tax_total: string; total: string; notes: string; lines: PLine[] };
const emptyLine = (): PLine => ({ ingredient_id: "", description: "", quantity: "", unit: "kg", unit_price: "", line_discount: "", line_total: "" });
const freshDraft = (): PDraft => ({ key: newKey(), supplier_id: "", invoice_ref: "", supplier_tax_ref: "", invoice_date: today(), due_date: "",
  discount_total: "", tax_total: "", total: "", notes: "", lines: [emptyLine()] });

export function NewPurchase() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const ings = useIngredients();
  const suppliers = useQuery({ queryKey: ["suppliers"], queryFn: () => get<Supplier[]>("/suppliers") });
  const [d, setD, clearDraft, restored] = useDraft<PDraft>("purchase-draft", freshDraft());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const byId = Object.fromEntries((ings.data || []).map((i) => [i.id, i])) as Record<string, Ingredient>;
  const set = (p: Partial<PDraft>) => setD({ ...d, ...p });
  const setLine = (i: number, p: Partial<PLine>) => set({ lines: d.lines.map((l, j) => (j === i ? { ...l, ...p } : l)) });
  const lines = d.lines.filter((l) => l.ingredient_id && num(l.quantity) > 0);
  const lineTotal = (l: PLine) => Math.max(0, mul(l.quantity, l.unit_price) - (num(l.line_discount) || 0));
  const subtotal = lines.reduce((s, l) => s + lineTotal(l), 0);
  const total = subtotal - (num(d.discount_total) || 0) + (num(d.tax_total) || 0);
  const body = () => ({
    supplier_id: d.supplier_id, invoice_ref: d.invoice_ref, supplier_tax_ref: d.supplier_tax_ref || null, invoice_date: d.invoice_date,
    due_date: d.due_date || null, discount_total: d.discount_total || "0", tax_total: d.tax_total || "0", total: d.total || null, notes: d.notes || null,
    lines: lines.map((l) => ({ ingredient_id: l.ingredient_id, description: l.description || null, quantity: l.quantity, unit: l.unit,
                               unit_price: l.unit_price || "0", line_discount: l.line_discount || "0", line_total: l.line_total || null })),
  });
  const validate = useMutation({ mutationFn: () => post("/purchases/validate", body()), onSuccess: () => setConfirmOpen(true) });
  const confirm = useMutation({
    mutationFn: () => post("/purchases", body(), d.key),
    onSuccess: (p: any) => {
      clearDraft(); qc.invalidateQueries();
      toast("good", `Purchase ${p.invoice_ref} confirmed — stock added and supplier balance updated`);
      nav(`/purchases/${p.id}`);
    },
  });
  const supplier = suppliers.data?.find((s) => s.id === d.supplier_id);
  const supplierIngs = new Set((ings.data || []).filter((i) => i.suppliers.some((s) => s.supplier_id === d.supplier_id)).map((i) => i.id));
  const ingList = [...(ings.data || [])].filter((i) => i.is_active).sort((a, b) => Number(supplierIngs.has(b.id)) - Number(supplierIngs.has(a.id)));
  return (
    <Page title="Record purchase" subtitle="Enter the supplier invoice. Nothing is posted until you confirm.">
      {restored && <div className="alert info">Restored your unsaved purchase from this device. <button className="btn ghost sm" onClick={() => { clearDraft(); setD(freshDraft()); }}>Start over</button></div>}
      <ErrorBox error={validate.error} />
      <form onSubmit={(e) => { e.preventDefault(); validate.mutate(); }} className="stack">
        <Card title="Invoice">
          <div className="form-grid">
            <Field label="Supplier">{(i) => <SearchSelect id={i} required items={suppliers.data?.filter((s) => s.is_active) || []} value={d.supplier_id} label={(s) => s.name} placeholder="Choose supplier…" onChange={(v) => set({ supplier_id: v })} />}</Field>
            <Field label="Invoice number">{(i) => <input id={i} required value={d.invoice_ref} onChange={(e) => set({ invoice_ref: e.target.value })} />}</Field>
            <Field label="Invoice date">{(i) => <input id={i} type="date" required value={d.invoice_date} max={today()} onChange={(e) => set({ invoice_date: e.target.value })} />}</Field>
            <Field label="Due date (optional)">{(i) => <input id={i} type="date" value={d.due_date} onChange={(e) => set({ due_date: e.target.value })} />}</Field>
            <Field label="Fiscal ref. (e.g. EFRIS FDN)" hint="Optional tax-authority invoice number">{(i) => <input id={i} value={d.supplier_tax_ref} onChange={(e) => set({ supplier_tax_ref: e.target.value })} />}</Field>
          </div>
        </Card>
        <Card title="Items" pad={false}>
          <div className="table-wrap"><table className="line-table">
            <thead><tr><th style={{ minWidth: 190 }}>Ingredient</th><th style={{ width: 100 }}>Qty</th><th style={{ width: 84 }}>Unit</th><th style={{ width: 120 }}>Unit price</th><th style={{ width: 100 }} className="hide-sm">Discount</th><th style={{ width: 120 }} className="hide-sm">Line total on invoice</th><th className="num">Line total</th><th /></tr></thead>
            <tbody>{d.lines.map((l, i) => {
              const ing = byId[l.ingredient_id];
              const lt = lineTotal(l);
              const mismatch = l.line_total !== "" && Math.abs(num(l.line_total) - lt) > 0.01;
              return (<tr key={i}>
                <td><SearchSelect items={ingList} value={l.ingredient_id} label={(x) => `${x.name}${supplierIngs.has(x.id) ? " ★" : ""}`} placeholder="Ingredient…"
                  onChange={(v) => setLine(i, { ingredient_id: v, unit: byId[v]?.purchase_unit || "kg" })} /></td>
                <td><input type="number" step="any" min="0" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} aria-label="Quantity" placeholder="Qty" /></td>
                <td><select value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} aria-label="Unit">{unitsFor(ing).map((u) => <option key={u}>{u}</option>)}</select></td>
                <td><input type="number" step="any" min="0" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} aria-label="Unit price" placeholder="Unit price" /></td>
                <td className="hide-sm"><input type="number" step="any" min="0" value={l.line_discount} onChange={(e) => setLine(i, { line_discount: e.target.value })} aria-label="Line discount" /></td>
                <td className="hide-sm"><input type="number" step="any" min="0" value={l.line_total} placeholder="optional" onChange={(e) => setLine(i, { line_total: e.target.value })} aria-label="Line total printed on invoice" style={mismatch ? { borderColor: "var(--bad)" } : undefined} /></td>
                <td className="num">{money(lt)}{mismatch && <div className="field-error">≠ invoice</div>}</td>
                <td><button type="button" className="icon-btn" aria-label="Remove line" onClick={() => set({ lines: d.lines.filter((_, j) => j !== i) })}>×</button></td>
              </tr>);
            })}</tbody></table></div>
          <div className="card-body"><Button type="button" variant="secondary" className="sm" onClick={() => set({ lines: [...d.lines, emptyLine()] })}>+ Add item</Button>
            {d.supplier_id && supplierIngs.size > 0 && <span className="muted small" style={{ marginLeft: 10 }}>★ = usually bought from {supplier?.name}</span>}</div>
        </Card>
        <div className="grid cols-2">
          <Card title="Notes"><Field label="Purchase notes">{(i) => <textarea id={i} value={d.notes} onChange={(e) => set({ notes: e.target.value })} />}</Field></Card>
          <Card title="Totals">
            <div className="form-grid" style={{ marginBottom: 12 }}>
              <Field label="Invoice discount">{(i) => <input id={i} type="number" step="any" min="0" value={d.discount_total} onChange={(e) => set({ discount_total: e.target.value })} />}</Field>
              <Field label="Tax (VAT)" hint="Recoverable VAT is not added to stock cost">{(i) => <input id={i} type="number" step="any" min="0" value={d.tax_total} onChange={(e) => set({ tax_total: e.target.value })} />}</Field>
              <Field label="Total printed on invoice" hint="Optional — we check it adds up" error={d.total !== "" && Math.abs(num(d.total) - total) > 0.05 ? `Calculated total is ${money(total)}` : undefined}>
                {(i) => <input id={i} type="number" step="any" min="0" value={d.total} onChange={(e) => set({ total: e.target.value })} />}</Field>
            </div>
            <div className="totals"><span>Subtotal</span><span className="num">{money(subtotal)}</span>
              <span>Discount</span><span className="num">{money(-(num(d.discount_total) || 0))}</span>
              <span>Tax</span><span className="num">{money(num(d.tax_total) || 0)}</span>
              <span className="grand">Invoice total</span><span className="num grand">{money(total)}</span></div>
          </Card>
        </div>
        <div className="form-actions">
          <span className="muted small" style={{ marginRight: "auto" }}>Draft saved on this device · not posted</span>
          <Button type="button" variant="secondary" onClick={() => nav("/purchases")}>Close</Button>
          <Button type="submit" busy={validate.isPending} disabled={!d.supplier_id || !lines.length}>Review & confirm</Button>
        </div>
      </form>
      {confirmOpen && (
        <Confirm title="Confirm this purchase?" confirmLabel={`Confirm ${money(total)}`} busy={confirm.isPending} onClose={() => setConfirmOpen(false)} onConfirm={() => confirm.mutate()}>
          {confirm.error instanceof NetworkError && <div className="alert warn">The server could not be reached. Your purchase is still saved on this device. Press confirm again when the connection is back — it will not be recorded twice.</div>}
          {!(confirm.error instanceof NetworkError) && <ErrorBox error={confirm.error} />}
          <p><strong>{supplier?.name}</strong> · invoice {d.invoice_ref} · {date(d.invoice_date)}</p>
          <ul>{lines.map((l, i) => <li key={i}>{qty(l.quantity, l.unit)} {byId[l.ingredient_id]?.name} — {money(lineTotal(l))}</li>)}</ul>
          <p className="muted small">This will add {lines.length} stock batch(es), update ingredient prices and recipe costs, and add {money(total)} to the amount owed to {supplier?.name}. It cannot be edited afterwards (it can be voided if the stock is unused).</p>
        </Confirm>)}
    </Page>
  );
}

export function PurchaseDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [voidOpen, setVoidOpen] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [voidKey] = useState(newKey);
  const q = useQuery({ queryKey: ["purchase", id], queryFn: () => get(`/purchases/${id}`) });
  const v = useMutation({
    mutationFn: () => post(`/purchases/${id}/void`, { reason }, voidKey),
    onSuccess: () => { qc.invalidateQueries(); toast("good", "Purchase voided — stock, prices and balance reversed"); setVoidOpen(false); },
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const p = q.data;
  return (
    <Page title={`Invoice ${p.invoice_ref}`} subtitle={<><Link to={`/suppliers/${p.supplier_id}`}>{p.supplier_name}</Link> · {date(p.invoice_date)} · recorded {dateTime(p.created_at)}</>}
      actions={<>
        <StatusBadge status={p.payment_status} />
        <SyncBadge status={p.sync_status} />
        {p.status === "confirmed" && Number(p.outstanding) > 0 && can("payment.post") && <Button onClick={() => setPayOpen(true)}>Record payment</Button>}
        {p.status === "confirmed" && can("purchase.void") && <Button variant="secondary" onClick={() => setVoidOpen(true)}>Void</Button>}
      </>}>
      {p.status === "voided" && <div className="alert warn">Voided: {p.void_reason}. Its stock, price history and supplier balance entries have been reversed.</div>}
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="stat card"><div className="stat-label">Invoice total</div><div className="stat-value">{money(p.total)}</div></div>
        <div className="stat card"><div className="stat-label">Paid</div><div className="stat-value good">{money(p.amount_paid)}</div></div>
        <div className="stat card"><div className="stat-label">Credits</div><div className="stat-value">{money(p.amount_credited)}</div></div>
        <div className="stat card"><div className="stat-label">Outstanding</div><div className={`stat-value ${Number(p.outstanding) > 0 ? "warn" : ""}`}>{money(p.outstanding)}</div></div>
      </div>
      <div className="stack">
        <Card title="Items" pad={false}>
          <div className="table-wrap"><table><thead><tr><th>Ingredient</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num hide-sm">Discount</th><th className="num">Line total</th><th className="num hide-sm">Stock cost</th><th className="num hide-sm">Cost / base unit</th></tr></thead>
            <tbody>{p.lines.map((l: any) => (
              <tr key={l.id}><td><Link to={`/ingredients/${l.ingredient_id}`}>{l.ingredient_name}</Link>{l.description && <div className="muted small">{l.description}</div>}</td>
                <td className="num">{qty(l.quantity, l.unit)}<div className="muted small">{smartQty(l.base_quantity, l.base_unit)}</div></td>
                <td className="num">{money(l.unit_price)}</td><td className="num hide-sm">{money(l.line_discount)}</td><td className="num">{money(l.line_total)}</td>
                <td className="num hide-sm">{money(l.stock_cost)}</td><td className="num hide-sm">{unitCost(l.cost_per_base_unit, l.base_unit)}</td></tr>))}</tbody></table></div>
          <div className="card-body"><div className="totals">
            <span>Subtotal</span><span className="num">{money(p.subtotal)}</span><span>Discount</span><span className="num">{money(-Number(p.discount_total))}</span>
            <span>Tax</span><span className="num">{money(p.tax_total)}</span><span className="grand">Total</span><span className="num grand">{money(p.total)}</span></div>
            <p className="muted small">"Stock cost" spreads the invoice discount over the items{Number(p.tax_total) > 0 ? " and excludes recoverable VAT" : ""}; it is the cost each FIFO batch carries. <BasisTag basis="fifo" /></p></div>
        </Card>
        <Attachments purchaseId={p.id} canUpload={can("purchase.create") && p.status === "confirmed"} />
        <Card title="Payments" pad={false}>
          {!p.payments.length ? <Empty title="No payments yet" /> : (
            <div className="table-wrap"><table><thead><tr><th>Date</th><th>Method</th><th className="hide-sm">Reference</th><th className="num">Applied to this invoice</th><th>Status</th></tr></thead>
              <tbody>{p.payments.map((x: any) => (<tr key={x.id} style={x.status === "voided" ? { opacity: .55 } : undefined}><td>{date(x.paid_on)}</td><td>{x.method.replace(/_/g, " ")}</td><td className="hide-sm">{x.reference || "—"}</td>
                <td className="num">{money(x.allocated)}</td><td><StatusBadge status={x.status} /> <SyncBadge status={x.sync_status} /></td></tr>))}</tbody></table></div>)}
        </Card>
        {p.notes && <Card title="Notes"><p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{p.notes}</p></Card>}
      </div>
      {voidOpen && (
        <Confirm title="Void this purchase?" danger confirmLabel="Void purchase" busy={v.isPending} onClose={() => setVoidOpen(false)} onConfirm={() => reason.trim().length >= 3 && v.mutate()}>
          <ErrorBox error={v.error} />
          <p>This removes its stock batches, reverses its price history and supplier balance. Only possible if none of the stock has been used and no payments are applied.</p>
          <Field label="Reason (required)">{(i) => <input id={i} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Entered twice / wrong supplier" />}</Field>
        </Confirm>)}
      {payOpen && <PaymentModal supplierId={p.supplier_id} preselect={p.id} onClose={() => setPayOpen(false)} />}
    </Page>
  );
}

function Attachments({ purchaseId, canUpload }: { purchaseId: string; canUpload: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["attachments", purchaseId], queryFn: () => get<any[]>(`/purchases/${purchaseId}/attachments`) });
  const up = useMutation({
    mutationFn: (f: File) => uploadFile(`/purchases/${purchaseId}/attachments`, f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["attachments", purchaseId] }); qc.invalidateQueries({ queryKey: ["health"] }); toast("good", "Receipt saved on the restaurant server"); },
  });
  const [openErr, setOpenErr] = useState<unknown>(null);
  return (
    <Card title="Receipts & documents" pad={false} actions={canUpload && (
      <label className="btn secondary sm" style={{ cursor: "pointer" }}>
        {up.isPending ? <span className="spinner" /> : null} Add photo or PDF
        <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" capture="environment" hidden
          onChange={(e) => { const f = e.target.files?.[0]; if (f) up.mutate(f); e.target.value = ""; }} />
      </label>)}>
      <ErrorBox error={up.error || openErr} />
      {!q.data?.length ? <Empty title="No receipt attached">Photograph the supplier invoice so the original is kept with this purchase.</Empty> : (
        <ul className="list-plain">{q.data.map((f) => (
          <li key={f.id}><span><button className="btn ghost sm" onClick={() => openFile(f.id).catch(setOpenErr)}>{f.name}</button>
            <div className="muted small">{(f.size_bytes / 1024).toFixed(0)} KB · {dateTime(f.created_at)} · SHA-256 {f.sha256.slice(0, 12)}…</div></span>
            <span className="row" style={{ gap: 6 }}><span className="badge good">saved locally</span>
              {f.cloud_status === "verified" ? <span className="badge good" title={`Checksum verified ${dateTime(f.cloud_verified_at)}`}>in cloud ✓</span>
                : <span className="badge warn" title="Will be uploaded and checksum-verified on the next sync">not yet in cloud</span>}</span></li>))}</ul>)}
    </Card>
  );
}
