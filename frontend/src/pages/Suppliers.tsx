import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, Empty, ErrorBox, Field, Loading, Modal, Page, StatusBadge, Tabs, useToast } from "../components/ui";
import { NetworkError, get, newKey, post, put } from "../lib/api";
import { useAuth } from "../lib/auth";
import { date, dateTime, money, num, today } from "../lib/format";
import type { Supplier } from "../lib/types";

export function SuppliersPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const q = useQuery({ queryKey: ["suppliers"], queryFn: () => get<Supplier[]>("/suppliers") });
  const rows = (q.data || []).filter((s) => !search || s.name.toLowerCase().includes(search.toLowerCase()));
  const owed = (q.data || []).reduce((s, x) => s + num(x.balance), 0);
  return (
    <Page title="Suppliers" subtitle={<>Total owed to suppliers: <strong>{money(owed)}</strong></>}
      actions={can("supplier.write") && <Button onClick={() => setOpen(true)}>New supplier</Button>}>
      <Card pad={false}>
        <div className="card-body" style={{ paddingBottom: 0 }}><div className="toolbar">
          <input type="search" placeholder="Search suppliers…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search suppliers" /></div></div>
        {q.isLoading ? <Loading /> : rows.length === 0 ? <Empty title="No suppliers yet">Add the suppliers you buy from.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Supplier</th><th className="hide-sm">Contact</th><th className="num">Total purchases</th><th className="num">Balance owed</th></tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.id} className="clickable" onClick={() => nav(`/suppliers/${s.id}`)}>
                <td><Link to={`/suppliers/${s.id}`} onClick={(e) => e.stopPropagation()}>{s.name}</Link> {!s.is_active && <span className="badge neutral">inactive</span>}<div className="muted small mono">{s.code}</div></td>
                <td className="hide-sm">{s.contact_person || "—"}<div className="muted small">{s.phone}</div></td>
                <td className="num">{money(s.total_purchases)}</td>
                <td className="num"><strong className={num(s.balance) > 0 ? "" : "muted"}>{money(s.balance)}</strong></td></tr>))}</tbody>
          </table></div>)}
      </Card>
      {open && <SupplierModal onClose={() => setOpen(false)} />}
    </Page>
  );
}

function SupplierModal({ supplier, onClose }: { supplier?: Supplier; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const [f, setF] = useState<any>(supplier ? { ...supplier } : { code: "", name: "", tax_id: "", contact_person: "", phone: "", email: "", address: "", payment_terms_days: "", notes: "", is_active: true });
  const m = useMutation({
    mutationFn: () => {
      const body = { code: f.code, name: f.name, tax_id: f.tax_id || null, contact_person: f.contact_person || null, phone: f.phone || null,
        email: f.email || null, address: f.address || null, payment_terms_days: f.payment_terms_days === "" || f.payment_terms_days === null ? null : Number(f.payment_terms_days),
        notes: f.notes || null, is_active: f.is_active };
      return supplier ? put<Supplier>(`/suppliers/${supplier.id}`, body) : post<Supplier>("/suppliers", body);
    },
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["suppliers"] }); qc.invalidateQueries({ queryKey: ["supplier", s.id] }); toast("good", `Supplier ${s.name} saved`); onClose(); if (!supplier) nav(`/suppliers/${s.id}`); },
  });
  const field = (k: string, label: string, type = "text", extra: any = {}) => (
    <Field label={label}>{(i) => <input id={i} type={type} value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: type === "text" && k === "code" ? e.target.value.toUpperCase() : e.target.value })} {...extra} />}</Field>);
  return (
    <Modal title={supplier ? `Edit ${supplier.name}` : "New supplier"} onClose={onClose} wide>
      <ErrorBox error={m.error} />
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="form-grid">
          {field("name", "Name", "text", { required: true })}{field("code", "Code", "text", { required: true, pattern: "[A-Za-z0-9_\\-]+" })}
          {field("contact_person", "Contact person")}{field("phone", "Phone", "tel")}{field("email", "Email", "email")}
          {field("tax_id", "Tax ID (TIN)")}{field("payment_terms_days", "Payment terms (days)", "number", { min: 0, max: 365 })}
          <Field label="Status">{(i) => <select id={i} value={f.is_active ? "1" : "0"} onChange={(e) => setF({ ...f, is_active: e.target.value === "1" })}><option value="1">Active</option><option value="0">Inactive</option></select>}</Field>
        </div>
        <div className="grid cols-2" style={{ marginTop: 14 }}>
          <Field label="Address">{(i) => <textarea id={i} value={f.address ?? ""} onChange={(e) => setF({ ...f, address: e.target.value })} />}</Field>
          <Field label="Notes">{(i) => <textarea id={i} value={f.notes ?? ""} onChange={(e) => setF({ ...f, notes: e.target.value })} />}</Field>
        </div>
        <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={m.isPending}>Save supplier</Button></div>
      </form>
    </Modal>
  );
}

export function SupplierDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const [tab, setTab] = useState("invoices");
  const [edit, setEdit] = useState(false);
  const [pay, setPay] = useState(false);
  const [credit, setCredit] = useState(false);
  const q = useQuery({ queryKey: ["supplier", id], queryFn: () => get(`/suppliers/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const s = q.data;
  const open = s.invoices.filter((i: any) => num(i.outstanding) > 0);
  return (
    <Page title={s.name} subtitle={<><span className="mono">{s.code}</span>{s.contact_person && <> · {s.contact_person}</>}{s.phone && <> · {s.phone}</>}{s.email && <> · {s.email}</>}</>}
      actions={<>
        {can("supplier.write") && <Button variant="secondary" onClick={() => setEdit(true)}>Edit</Button>}
        {can("payment.post") && open.length > 0 && <Button variant="secondary" onClick={() => setCredit(true)}>Record credit</Button>}
        {can("payment.post") && open.length > 0 && <Button onClick={() => setPay(true)}>Record payment</Button>}
      </>}>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="stat card"><div className="stat-label">Balance owed</div><div className={`stat-value ${num(s.balance) > 0 ? "warn" : "good"}`}>{money(s.balance)}</div></div>
        <div className="stat card"><div className="stat-label">Total purchases</div><div className="stat-value">{money(s.total_purchases)}</div></div>
        <div className="stat card"><div className="stat-label">Total paid</div><div className="stat-value">{money(s.total_paid)}</div></div>
        <div className="stat card"><div className="stat-label">Open invoices</div><div className="stat-value">{open.length}</div><div className="stat-hint">{s.payment_terms_days ? `Terms: ${s.payment_terms_days} days` : ""}</div></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "invoices", label: "Invoices" }, { id: "payments", label: "Payments" }, { id: "statement", label: "Statement" }, { id: "info", label: "Profile" }]} />
      {tab === "invoices" && (
        <Card pad={false}>{!s.invoices.length ? <Empty title="No purchases from this supplier yet" /> : (
          <div className="table-wrap"><table><thead><tr><th>Date</th><th>Invoice</th><th className="num">Total</th><th className="num hide-sm">Paid</th><th className="num">Outstanding</th><th>Status</th></tr></thead>
            <tbody>{s.invoices.map((i: any) => (<tr key={i.id}><td>{date(i.invoice_date)}{i.due_date && <div className="muted small">due {date(i.due_date)}</div>}</td><td><Link to={`/purchases/${i.id}`}>{i.invoice_ref}</Link></td>
              <td className="num">{money(i.total)}</td><td className="num hide-sm">{money(i.amount_paid)}</td><td className="num">{money(i.outstanding)}</td><td><StatusBadge status={i.payment_status} /></td></tr>))}</tbody></table></div>)}
        </Card>)}
      {tab === "payments" && <PaymentsTable payments={s.payments} invoices={s.invoices} />}
      {tab === "statement" && (
        <Card pad={false} title="Account statement">{!s.statement.length ? <Empty title="No transactions" /> : (
          <div className="table-wrap"><table><thead><tr><th>Posted</th><th>Description</th><th className="num">Amount</th><th className="num">Balance</th></tr></thead>
            <tbody>{s.statement.map((e: any, i: number) => (<tr key={i}><td className="small">{dateTime(e.posted_at)}</td><td>{e.description}</td>
              <td className="num" style={{ color: num(e.amount) < 0 ? "var(--good)" : undefined }}>{money(e.amount, { sign: true })}</td><td className="num"><strong>{money(e.balance)}</strong></td></tr>))}</tbody></table></div>)}
        </Card>)}
      {tab === "info" && (
        <Card><dl className="kv"><dt>Tax ID</dt><dd>{s.tax_id || "—"}</dd><dt>Address</dt><dd style={{ whiteSpace: "pre-wrap" }}>{s.address || "—"}</dd>
          <dt>Payment terms</dt><dd>{s.payment_terms_days ? `${s.payment_terms_days} days` : "—"}</dd><dt>Notes</dt><dd style={{ whiteSpace: "pre-wrap" }}>{s.notes || "—"}</dd>
          <dt>Status</dt><dd>{s.is_active ? "Active" : "Inactive"}</dd></dl></Card>)}
      {edit && <SupplierModal supplier={s} onClose={() => setEdit(false)} />}
      {pay && <PaymentModal supplierId={s.id} onClose={() => setPay(false)} />}
      {credit && <CreditModal invoices={open} onClose={() => setCredit(false)} />}
    </Page>
  );
}

function PaymentsTable({ payments, invoices }: { payments: any[]; invoices: any[] }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [voiding, setVoiding] = useState<any>(null);
  const [reason, setReason] = useState("");
  const [key] = useState(newKey);
  const refs = Object.fromEntries(invoices.map((i) => [i.id, i.invoice_ref]));
  const v = useMutation({ mutationFn: () => post(`/payments/${voiding.id}/void`, { reason }, key + voiding.id),
    onSuccess: () => { qc.invalidateQueries(); toast("good", "Payment voided"); setVoiding(null); } });
  return (
    <Card pad={false}>{!payments.length ? <Empty title="No payments yet" /> : (
      <div className="table-wrap"><table><thead><tr><th>Date</th><th>Method</th><th className="hide-sm">Reference</th><th>Applied to</th><th className="num">Amount</th><th>Status</th><th /></tr></thead>
        <tbody>{payments.map((p) => (<tr key={p.id} style={p.status === "voided" ? { opacity: .55 } : undefined}><td>{date(p.paid_on)}</td><td>{p.method.replace(/_/g, " ")}</td><td className="hide-sm">{p.reference || "—"}</td>
          <td className="small">{p.allocations.map((a: any) => `${refs[a.invoice_id] || "?"}: ${money(a.amount)}`).join(", ")}</td><td className="num">{money(p.amount)}</td>
          <td><StatusBadge status={p.status} />{p.void_reason && <div className="muted small">{p.void_reason}</div>}</td>
          <td>{p.status === "confirmed" && can("payment.void") && <Button variant="ghost" className="sm" onClick={() => setVoiding(p)}>Void</Button>}</td></tr>))}</tbody></table></div>)}
      {voiding && (
        <Modal title="Void payment" onClose={() => setVoiding(null)}>
          <ErrorBox error={v.error} />
          <p>Voiding restores {money(voiding.amount)} to the supplier balance. The payment stays in history.</p>
          <Field label="Reason">{(i) => <input id={i} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Bank transfer bounced" />}</Field>
          <div className="form-actions"><Button variant="secondary" onClick={() => setVoiding(null)}>Cancel</Button><Button variant="danger" busy={v.isPending} disabled={reason.trim().length < 3} onClick={() => v.mutate()}>Void payment</Button></div>
        </Modal>)}
    </Card>
  );
}

export function PaymentModal({ supplierId, preselect, onClose }: { supplierId: string; preselect?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const s = useQuery({ queryKey: ["supplier", supplierId], queryFn: () => get(`/suppliers/${supplierId}`) });
  const [key] = useState(newKey);
  const [f, setF] = useState({ paid_on: today(), method: "cash", reference: "", notes: "", amount: "" });
  const [alloc, setAlloc] = useState<Record<string, string>>({});
  const open: any[] = (s.data?.invoices || []).filter((i: any) => num(i.outstanding) > 0).sort((a: any, b: any) => a.invoice_date.localeCompare(b.invoice_date));
  const allocated = Object.values(alloc).reduce((t, v) => t + (num(v) || 0), 0);
  const autoAllocate = (amt: string) => {
    let left = num(amt) || 0; const next: Record<string, string> = {};
    const order = preselect ? [...open.filter((i) => i.id === preselect), ...open.filter((i) => i.id !== preselect)] : open;
    for (const i of order) { if (left <= 0) break; const take = Math.min(left, num(i.outstanding)); next[i.id] = String(+take.toFixed(4)); left -= take; }
    setAlloc(next);
  };
  const m = useMutation({
    mutationFn: () => post("/payments", { supplier_id: supplierId, paid_on: f.paid_on, amount: f.amount, method: f.method, reference: f.reference || null, notes: f.notes || null,
      allocations: Object.entries(alloc).filter(([, v]) => num(v) > 0).map(([invoice_id, amount]) => ({ invoice_id, amount })) }, key),
    onSuccess: (r: any) => { qc.invalidateQueries(); toast("good", `Payment of ${money(r.amount)} posted. New balance ${money(r.supplier_balance)}`); onClose(); },
  });
  const mismatch = f.amount !== "" && Math.abs(allocated - num(f.amount)) > 0.00005;
  return (
    <Modal title={`Record payment — ${s.data?.name ?? ""}`} onClose={onClose} wide>
      {m.error instanceof NetworkError ? <div className="alert warn">Server unreachable. Nothing was posted yet. Try again when connected — it will not be recorded twice.</div> : <ErrorBox error={m.error} />}
      {s.isLoading ? <Loading /> : (
        <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div className="form-grid">
            <Field label="Amount paid">{(i) => <input id={i} type="number" step="any" min="0" required value={f.amount} onChange={(e) => { setF({ ...f, amount: e.target.value }); autoAllocate(e.target.value); }} />}</Field>
            <Field label="Payment date">{(i) => <input id={i} type="date" required max={today()} value={f.paid_on} onChange={(e) => setF({ ...f, paid_on: e.target.value })} />}</Field>
            <Field label="Method">{(i) => <select id={i} value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
              <option value="cash">Cash</option><option value="mobile_money">Mobile money</option><option value="bank_transfer">Bank transfer</option><option value="cheque">Cheque</option><option value="card">Card</option><option value="other">Other</option></select>}</Field>
            <Field label="Reference" hint="Transaction ID, cheque no. — prevents duplicates">{(i) => <input id={i} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} />}</Field>
          </div>
          <h3 style={{ marginTop: 18 }}>Apply to invoices</h3>
          <div className="table-wrap"><table className="line-table"><thead><tr><th>Invoice</th><th>Date</th><th className="num">Outstanding</th><th style={{ width: 150 }}>Apply</th></tr></thead>
            <tbody>{open.map((i) => (<tr key={i.id}><td>{i.invoice_ref}</td><td>{date(i.invoice_date)}</td><td className="num">{money(i.outstanding)}</td>
              <td><input type="number" step="any" min="0" max={i.outstanding} value={alloc[i.id] ?? ""} aria-label={`Amount for ${i.invoice_ref}`}
                onChange={(e) => setAlloc({ ...alloc, [i.id]: e.target.value })} style={num(alloc[i.id]) > num(i.outstanding) ? { borderColor: "var(--bad)" } : undefined} /></td></tr>))}</tbody>
            <tfoot><tr><td colSpan={3}>Allocated</td><td className="num">{money(allocated)}</td></tr></tfoot></table></div>
          {mismatch && <div className="field-error" style={{ marginTop: 8 }}>Allocated {money(allocated)} must equal the amount paid {money(f.amount)}.</div>}
          <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="submit" busy={m.isPending} disabled={!f.amount || mismatch || allocated <= 0}>Post payment</Button></div>
        </form>)}
    </Modal>
  );
}

function CreditModal({ invoices, onClose }: { invoices: any[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [key] = useState(newKey);
  const [f, setF] = useState({ invoice_id: invoices[0]?.id || "", amount: "", reason: "", reference: "" });
  const m = useMutation({ mutationFn: () => post("/supplier-credits", { ...f, reference: f.reference || null }, key),
    onSuccess: () => { qc.invalidateQueries(); toast("good", "Credit recorded"); onClose(); } });
  return (
    <Modal title="Record supplier credit" onClose={onClose}>
      <p className="muted small" style={{ marginTop: 0 }}>For returns, short deliveries or credit notes. Reduces what you owe on an invoice. It does not change stock.</p>
      <ErrorBox error={m.error} />
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="form-grid">
          <Field label="Invoice">{(i) => <select id={i} value={f.invoice_id} onChange={(e) => setF({ ...f, invoice_id: e.target.value })}>{invoices.map((x) => <option key={x.id} value={x.id}>{x.invoice_ref} ({money(x.outstanding)})</option>)}</select>}</Field>
          <Field label="Amount">{(i) => <input id={i} type="number" step="any" min="0" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
          <Field label="Credit note ref.">{(i) => <input id={i} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} />}</Field>
        </div>
        <div style={{ marginTop: 12 }}><Field label="Reason">{(i) => <input id={i} required minLength={3} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} placeholder="e.g. 2 kg returned — spoiled" />}</Field></div>
        <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={m.isPending}>Record credit</Button></div>
      </form>
    </Modal>
  );
}
