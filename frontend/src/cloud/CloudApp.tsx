import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorBox, Field, Loading, Modal, Page, SearchSelect, Tabs, useToast } from "../components/ui";
import { configureMoney, date, dateTime, money, pct } from "../lib/format";
import { capi, cloudToken } from "./api";

const SYNC: Record<string, [any, string]> = {
  in_sync: ["good", "In sync"], catching_up: ["info", "Catching up"], offline: ["warn", "Not seen recently"],
  error: ["bad", "Sync error"], halted: ["bad", "Halted"], awaiting_registration: ["neutral", "Awaiting registration"],
};
const SyncBadge = ({ s }: { s: string }) => { const [t, l] = SYNC[s] || ["neutral", s]; return <Badge tone={t}>{l}</Badge>; };

export default function CloudApp() {
  const [authed, setAuthed] = useState(!!cloudToken.get());
  if (!authed) return <CloudLogin onDone={() => setAuthed(true)} />;
  return (
    <div className="shell" style={{ gridTemplateColumns: "1fr" }}>
      <div className="main">
        <header className="topbar">
          <div className="row"><img src="/icon.svg" alt="" width={26} /><strong>KitchenLedger Cloud</strong><Link to="/">All branches</Link></div>
          <button className="btn secondary sm" onClick={() => { cloudToken.set(null); setAuthed(false); }}>Sign out</button>
        </header>
        <main className="content" style={{ margin: "0 auto" }}>
          <Routes>
            <Route index element={<Overview />} />
            <Route path="branches/:id" element={<BranchView />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function CloudLogin({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ email: "", password: "", code: "" });
  const [stage, setStage] = useState<"password" | "code" | "enroll">("password");
  const [enroll, setEnroll] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      if (stage === "enroll") {
        const r = await capi("/auth/mfa/enroll", { enrollment_token: enroll.enrollment_token, code: f.code });
        cloudToken.set(r.access_token); onDone(); return;
      }
      const r = await capi("/auth/login", { email: f.email, password: f.password, code: f.code || undefined });
      if (r.mfa_required) setStage("code");
      else if (r.mfa_enrollment_required) { setEnroll(r); setStage("enroll"); setF({ ...f, code: "" }); }
      else { cloudToken.set(r.access_token); onDone(); }
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand"><img src="/icon.svg" alt="" width={30} /> KitchenLedger Cloud</div>
        <h1>{stage === "enroll" ? "Set up two-step sign-in" : "Remote sign in"}</h1>
        <p className="muted">Remote access always requires a code from an authenticator app.</p>
        <ErrorBox error={error} />
        {stage === "enroll" ? (
          <div className="stack">
            <p className="small">Add this key to Google Authenticator, Authy or similar, then enter the 6-digit code.</p>
            <div className="mono" style={{ wordBreak: "break-all", padding: 10, background: "var(--surface-2)", borderRadius: 8 }}>{enroll.secret}</div>
            <a className="small" href={enroll.otpauth_uri}>Open in authenticator app</a>
            <Field label="6-digit code">{(i) => <input id={i} inputMode="numeric" autoComplete="one-time-code" required value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />}</Field>
          </div>
        ) : (
          <div className="stack">
            <Field label="Email">{(i) => <input id={i} type="email" required value={f.email} disabled={stage === "code"} onChange={(e) => setF({ ...f, email: e.target.value })} />}</Field>
            <Field label="Password">{(i) => <input id={i} type="password" required value={f.password} disabled={stage === "code"} onChange={(e) => setF({ ...f, password: e.target.value })} />}</Field>
            {stage === "code" && <Field label="Authenticator code">{(i) => <input id={i} inputMode="numeric" autoComplete="one-time-code" autoFocus required value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />}</Field>}
          </div>)}
        <div className="form-actions"><Button busy={busy} type="submit" style={{ width: "100%" }}>{stage === "password" ? "Continue" : "Sign in"}</Button></div>
      </form>
    </div>
  );
}

function Overview() {
  const q = useQuery({ queryKey: ["cloud-summary"], queryFn: () => capi("/summary"), refetchInterval: 30000 });
  const branches = useQuery({ queryKey: ["cloud-branches"], queryFn: () => capi<any[]>("/branches") });
  const me = useQuery({ queryKey: ["cloud-me"], queryFn: () => capi("/me") });
  const [open, setOpen] = useState(false);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const waiting = (branches.data || []).filter((b) => b.status === "awaiting_registration");
  return (
    <Page title="All branches" subtitle={<>Central view from the cloud copies · {dateTime(q.data.as_of)}. Figures are as of each branch's last sync.</>}
      actions={me.data?.role === "admin" && <Button onClick={() => setOpen(true)}>Add branch</Button>}>
      <Card pad={false} title="Branches">
        {q.data.branches.length === 0 && waiting.length === 0 ? <Empty title="No branches yet">Add a branch and enter its registration code on the restaurant server.</Empty> : (
          <div className="table-wrap"><table><thead><tr><th>Branch</th><th>Sync</th><th className="hide-sm">Data as of</th><th className="num">Inventory value</th><th className="num">Owed to suppliers</th><th className="num hide-sm">Purchases (month)</th><th className="num hide-sm">Low stock</th></tr></thead>
            <tbody>{q.data.branches.map((b: any) => (<tr key={b.id}><td><Link to={`/branches/${b.id}`}>{b.name}</Link><div className="muted small">{b.code} · {b.currency}</div></td>
              <td><SyncBadge s={b.sync.state} /></td><td className="hide-sm small">{dateTime(b.sync.data_as_of)}</td>
              <td className="num">{b.currency} {fmt(b.inventory_value)}</td><td className="num">{b.currency} {fmt(b.outstanding_payables)}</td>
              <td className="num hide-sm">{b.currency} {fmt(b.month_purchases)}</td><td className="num hide-sm">{b.low_stock ?? "—"}</td></tr>))}
              {waiting.map((b) => (<tr key={b.id}><td><Link to={`/branches/${b.id}`}>{b.name}</Link><div className="muted small">{b.code}</div></td><td><SyncBadge s="awaiting_registration" /></td><td colSpan={5} className="muted small">Enter the registration code on the branch server (Settings → Cloud sync).</td></tr>))}</tbody></table></div>)}
      </Card>
      <p className="muted small">Totals are shown per branch currency; there is no currency conversion.</p>
      {open && <AddBranch onClose={() => setOpen(false)} />}
    </Page>
  );
}
const fmt = (v: any) => (v === undefined || v === null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));

function AddBranch({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ code: "", name: "" });
  const m = useMutation({ mutationFn: () => capi("/branches", f), onSuccess: () => qc.invalidateQueries() });
  return (
    <Modal title="Add branch" onClose={onClose}>
      <ErrorBox error={m.error} />
      {m.data ? (
        <div className="stack"><p>Enter this one-time code on the branch server under <strong>Settings → Cloud sync</strong>, together with this cloud's address:</p>
          <div className="mono" style={{ fontSize: "1.4rem", padding: 12, background: "var(--surface-2)", borderRadius: 8, textAlign: "center" }}>{m.data.registration_code}</div>
          <p className="muted small">Valid until {dateTime(m.data.registration_expires_at)}. It is shown only once.</p>
          <div className="form-actions"><Button onClick={onClose}>Done</Button></div></div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div className="form-grid">
            <Field label="Code">{(i) => <input id={i} required value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} placeholder="KLA" />}</Field>
            <Field label="Name">{(i) => <input id={i} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
          </div>
          <div className="form-actions"><Button type="button" variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={m.isPending}>Create & get code</Button></div>
        </form>)}
    </Modal>
  );
}

function BranchView() {
  const { id } = useParams();
  const [tab, setTab] = useState("dashboard");
  const b = useQuery({ queryKey: ["cloud-branch", id], queryFn: () => capi(`/branches/${id}`), refetchInterval: 15000 });
  if (b.isLoading) return <Loading />;
  if (b.error) return <ErrorBox error={b.error} />;
  const d = b.data;
  if (d.currency) configureMoney(d.currency, d.currency === "UGX" ? 0 : 2);
  return (
    <Page title={d.name} subtitle={<><SyncBadge s={d.sync.state} /> · data as of {dateTime(d.sync.data_as_of)} · last contact {dateTime(d.sync.last_seen_at)}</>}>
      {d.sync.halted_reason && <div className="alert bad"><strong>Sync halted:</strong> {d.sync.halted_reason}</div>}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "dashboard", label: "Dashboard" }, { id: "sync", label: "Sync monitor" }, { id: "remote", label: "Remote changes" }]} />
      {tab === "dashboard" && <BranchDashboard id={id!} />}
      {tab === "sync" && <SyncMonitor d={d} />}
      {tab === "remote" && <Remote id={id!} commands={d.commands} />}
    </Page>
  );
}

function BranchDashboard({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["cloud-dash", id], queryFn: () => capi(`/branches/${id}/dashboard`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  return (
    <>
      <div className="alert info">Read-only copy from the cloud, accurate up to the branch's last sync ({dateTime(d.sync.data_as_of)}). Changes made at the branch since then are not shown yet. Data checks: {d.integrity_ok ? "passed" : "FAILED"}.</div>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="stat card"><div className="stat-label">Inventory value</div><div className="stat-value">{money(d.inventory_value.value)}</div><div className="stat-hint">FIFO actual</div></div>
        <div className="stat card"><div className="stat-label">Purchases this month</div><div className="stat-value">{money(d.month_purchases)}</div></div>
        <div className="stat card"><div className="stat-label">Owed to suppliers</div><div className="stat-value warn">{money(d.outstanding_payables)}</div></div>
        <div className="stat card"><div className="stat-label">Low stock</div><div className={`stat-value ${d.low_stock.length ? "bad" : "good"}`}>{d.low_stock.length}</div></div>
      </div>
      <div className="grid cols-2">
        <Card title="Recipe food cost (current prices)" pad={false}>
          {!d.recipes.length ? <Empty title="No published recipes" /> : <div className="table-wrap"><table><thead><tr><th>Recipe</th><th className="num">Cost/portion</th><th className="num">Food cost</th></tr></thead>
            <tbody>{d.recipes.map((r: any) => <tr key={r.recipe_id}><td>{r.name}</td><td className="num">{money(r.cost_per_portion)}</td><td className="num">{r.food_cost_rate ? <Badge tone={r.exceeds_target ? "bad" : "good"}>{pct(r.food_cost_rate)}</Badge> : "—"}</td></tr>)}</tbody></table></div>}
        </Card>
        <Card title="Recent purchases" pad={false}>
          <ul className="list-plain">{d.recent_purchases.map((p: any) => <li key={p.id}><span>{p.invoice_ref}<div className="muted small">{p.supplier} · {date(p.invoice_date)}</div></span><span>{money(p.total)} <Badge tone={p.payment_status === "paid" ? "good" : p.payment_status === "unpaid" ? "bad" : "warn"}>{p.payment_status.replace("_", " ")}</Badge></span></li>)}</ul>
        </Card>
      </div>
    </>
  );
}

function SyncMonitor({ d }: { d: any }) {
  const q = d.sync.branch_queue || {};
  return (
    <div className="stack">
      <div className="grid cols-4">
        <div className="stat card"><div className="stat-label">Applied up to change #</div><div className="stat-value">{d.sync.last_applied_seq}</div><div className="stat-hint">{d.events_received} events received</div></div>
        <div className="stat card"><div className="stat-label">Waiting at branch</div><div className="stat-value">{(q.pending ?? 0) + (q.retry ?? 0)}</div><div className="stat-hint">{q.oldest_unsent_at ? `oldest ${dateTime(q.oldest_unsent_at)}` : "as last reported"}</div></div>
        <div className="stat card"><div className="stat-label">Rejected at branch</div><div className={`stat-value ${q.failed ? "bad" : "good"}`}>{q.failed ?? 0}</div></div>
        <div className="stat card"><div className="stat-label">Receipts</div><div className="stat-value">{d.files.verified ?? 0}</div><div className="stat-hint">verified · {q.files_pending ?? 0} still at branch · {d.files.uploading ?? 0} partial</div></div>
      </div>
      {d.sync.last_error && <div className="alert warn">Last apply error: {d.sync.last_error}</div>}
      <Card title="Most recent changes received" pad={false}>
        {!d.recent_events.length ? <Empty title="Nothing received yet" /> : <div className="table-wrap"><table><thead><tr><th>#</th><th>Operation</th><th className="num">Rows</th><th>At branch</th><th className="hide-sm">Received</th></tr></thead>
          <tbody>{d.recent_events.map((e: any) => <tr key={e.id}><td className="mono">{e.sequence}</td><td>{e.topic}{e.kind === "snapshot" && <Badge tone="info">snapshot</Badge>}</td><td className="num">{e.rows}</td><td className="small">{dateTime(e.created_at)}</td><td className="hide-sm small">{dateTime(e.received_at)}</td></tr>)}</tbody></table></div>}
      </Card>
    </div>
  );
}

const CMD_TONE: Record<string, any> = { queued: "neutral", delivered: "info", applied: "good", conflict: "warn", rejected: "bad" };

function Remote({ id, commands }: { id: string; commands: any[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const ings = useQuery({ queryKey: ["r-ings", id], queryFn: () => capi<any[]>(`/branches/${id}/replica/ingredients`) });
  const invs = useQuery({ queryKey: ["r-invs", id], queryFn: () => capi<any[]>(`/branches/${id}/replica/open-invoices`) });
  const settings = useQuery({ queryKey: ["r-settings", id], queryFn: () => capi(`/branches/${id}/replica/settings`) });
  const send = useMutation({ mutationFn: (c: { type: string; payload: any }) => capi(`/branches/${id}/commands`, c),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["cloud-branch", id] }); toast("info", "Sent — the branch will validate and apply it on its next sync"); } });
  const [ing, setIng] = useState({ id: "", min: "", active: "1" });
  const [price, setPrice] = useState({ id: "", qty: "1", amount: "", date: new Date().toISOString().slice(0, 10) });
  const [pay, setPay] = useState({ invoice: "", amount: "", method: "bank_transfer", reference: "" });
  const [target, setTarget] = useState("");
  const inv = invs.data?.find((x) => x.id === pay.invoice);
  const selIng = ings.data?.find((x) => x.id === ing.id);
  return (
    <div className="stack">
      <div className="alert info">Remote changes are requests. The branch applies them with its own rules. If someone at the branch changed the same thing first, the request is marked <strong>conflict</strong> and nothing is overwritten. Payments larger than the branch's live balance are <strong>rejected</strong>.</div>
      <ErrorBox error={send.error} />
      <div className="grid cols-2">
        <Card title="Pay a supplier invoice">
          <form onSubmit={(e) => { e.preventDefault(); send.mutate({ type: "payment.post", payload: { payment: { supplier_id: inv.supplier_id, paid_on: new Date().toISOString().slice(0, 10), amount: pay.amount, method: pay.method, reference: pay.reference || null, allocations: [{ invoice_id: inv.id, amount: pay.amount }] } } }); }}>
            <div className="form-grid">
              <Field label="Invoice">{(i) => <SearchSelect id={i} items={invs.data || []} value={pay.invoice} label={(x) => `${x.supplier} · ${x.invoice_ref} · owes ${fmt(x.outstanding)}`} onChange={(v) => setPay({ ...pay, invoice: v })} />}</Field>
              <Field label="Amount" hint={inv ? `Outstanding (as of last sync): ${fmt(inv.outstanding)}` : undefined}>{(i) => <input id={i} type="number" step="any" min="0" required value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} />}</Field>
              <Field label="Method">{(i) => <select id={i} value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}><option value="bank_transfer">Bank transfer</option><option value="mobile_money">Mobile money</option><option value="cheque">Cheque</option></select>}</Field>
              <Field label="Reference">{(i) => <input id={i} value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} />}</Field>
            </div>
            <div className="form-actions"><Button type="submit" disabled={!inv} busy={send.isPending}>Send payment</Button></div>
          </form>
        </Card>
        <Card title="Edit an ingredient">
          <form onSubmit={(e) => { e.preventDefault(); const changes: any = { is_active: ing.active === "1" }; if (ing.min) { changes.min_stock = ing.min; changes.min_stock_unit = selIng.purchase_unit; }
            send.mutate({ type: "ingredient.update", payload: { ingredient_id: ing.id, base_version: selIng.version, changes } }); }}>
            <div className="form-grid">
              <Field label="Ingredient">{(i) => <SearchSelect id={i} items={ings.data || []} value={ing.id} label={(x) => x.name} onChange={(v) => setIng({ ...ing, id: v })} />}</Field>
              <Field label={`Minimum stock${selIng ? ` (${selIng.purchase_unit})` : ""}`}>{(i) => <input id={i} type="number" step="any" min="0" value={ing.min} onChange={(e) => setIng({ ...ing, min: e.target.value })} />}</Field>
              <Field label="Status">{(i) => <select id={i} value={ing.active} onChange={(e) => setIng({ ...ing, active: e.target.value })}><option value="1">Active</option><option value="0">Inactive</option></select>}</Field>
            </div>
            <div className="form-actions"><Button type="submit" disabled={!selIng} busy={send.isPending}>Send change</Button></div>
          </form>
        </Card>
        <Card title="Record a quoted price">
          <form onSubmit={(e) => { e.preventDefault(); const g = ings.data!.find((x) => x.id === price.id);
            send.mutate({ type: "ingredient.price", payload: { ingredient_id: price.id, price: { quantity: price.qty, unit: g.purchase_unit, amount: price.amount, effective_date: price.date, note: "Quote entered remotely" } } }); }}>
            <div className="form-grid">
              <Field label="Ingredient">{(i) => <SearchSelect id={i} items={ings.data || []} value={price.id} label={(x) => `${x.name} (per ${x.purchase_unit})`} onChange={(v) => setPrice({ ...price, id: v })} />}</Field>
              <Field label="Quantity">{(i) => <input id={i} type="number" step="any" min="0" required value={price.qty} onChange={(e) => setPrice({ ...price, qty: e.target.value })} />}</Field>
              <Field label="Price">{(i) => <input id={i} type="number" step="any" min="0" required value={price.amount} onChange={(e) => setPrice({ ...price, amount: e.target.value })} />}</Field>
              <Field label="Effective date" hint="The newest date wins as the current cost">{(i) => <input id={i} type="date" required value={price.date} onChange={(e) => setPrice({ ...price, date: e.target.value })} />}</Field>
            </div>
            <div className="form-actions"><Button type="submit" disabled={!price.id} busy={send.isPending}>Send price</Button></div>
          </form>
        </Card>
        <Card title="Branch costing settings">
          <form onSubmit={(e) => { e.preventDefault(); send.mutate({ type: "branch_settings.update", payload: { base_version: settings.data.settings_version, changes: { default_target_food_cost_rate: String(Number(target) / 100) } } }); }}>
            <Field label="Default target food cost %" hint={settings.data ? `Currently ${pct(settings.data.default_target_food_cost_rate, 0)} (settings version ${settings.data.settings_version})` : undefined}>
              {(i) => <input id={i} type="number" step="any" min="1" max="99" required value={target} onChange={(e) => setTarget(e.target.value)} />}</Field>
            <div className="form-actions"><Button type="submit" disabled={!settings.data} busy={send.isPending}>Send change</Button></div>
          </form>
        </Card>
      </div>
      <Card title="Requests sent to this branch" pad={false}>
        {!commands.length ? <Empty title="No remote changes yet" /> : <div className="table-wrap"><table><thead><tr><th>Sent</th><th>Change</th><th>Status</th><th className="hide-sm">Result</th></tr></thead>
          <tbody>{commands.map((c) => <tr key={c.id}><td className="small">{dateTime(c.created_at)}<div className="muted">{c.created_by}</div></td><td>{c.type}</td>
            <td><Badge tone={CMD_TONE[c.status]}>{c.status}</Badge></td><td className="hide-sm small">{c.result?.error || (c.status === "applied" ? `applied ${dateTime(c.resolved_at)}` : c.status === "queued" ? "waiting for the branch to connect" : "")}</td></tr>)}</tbody></table></div>}
      </Card>
    </div>
  );
}
