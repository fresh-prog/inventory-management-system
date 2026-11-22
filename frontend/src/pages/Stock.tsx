import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  BasisTag, Button, Card, Confirm, Empty, ErrorBox, Field, Loading, Page, SearchSelect, StatusBadge, Tabs, useToast,
} from "../components/ui";
import { NetworkError, get, newKey, post, put } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDraft } from "../lib/hooks";
import { dateTime, money, num, qty, smartQty } from "../lib/format";
import type { Ingredient } from "../lib/types";
import { MovementTable, unitsFor, useIngredients } from "./Ingredients";

export function StockPage() {
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const tab = params.get("tab") || "levels";
  const tabs = [{ id: "levels", label: "Stock levels" },
    ...(can("stock.consume") ? [{ id: "use", label: "Record usage" }] : []),
    ...(can("production.record") ? [{ id: "produce", label: "Production" }] : []),
    ...(can("stock.adjust") ? [{ id: "waste", label: "Waste" }] : []),
    { id: "counts", label: "Stock counts" }, { id: "history", label: "Movements" }];
  return (
    <Page title="Stock & Inventory" subtitle="Stock is used oldest-first (FIFO). Every change is recorded and costed from the actual batches used.">
      <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={tabs} />
      {tab === "levels" && <Levels />}
      {tab === "use" && <Usage />}
      {tab === "produce" && <Production />}
      {tab === "waste" && <Waste />}
      {tab === "counts" && <Counts />}
      {tab === "history" && <History />}
    </Page>
  );
}

function Levels() {
  const [params, setParams] = useSearchParams();
  const filter = params.get("filter") || "";
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState(100);  // incremental rendering: thousands of rows stay fast on phones
  const q = useQuery({ queryKey: ["stock"], queryFn: () => get("/stock") });
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const all = q.data.items as any[];
  const rows = all.filter((r) => (filter !== "low" || r.is_low || (r.is_out && num(r.min_stock_base) > 0)) && (!search || r.name.toLowerCase().includes(search.toLowerCase())));
  const low = all.filter((r) => r.is_low).length;
  return (
    <>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="stat card"><div className="stat-label">Inventory value</div><div className="stat-value">{money(q.data.total_value)}</div><div className="stat-hint"><BasisTag basis="fifo" /></div></div>
        <div className="stat card"><div className="stat-label">Items in stock</div><div className="stat-value">{all.filter((r) => num(r.on_hand_base) > 0).length}</div><div className="stat-hint">of {all.length} active ingredients</div></div>
        <div className="stat card"><div className="stat-label">Below minimum</div><div className={`stat-value ${low ? "bad" : "good"}`}>{low}</div></div>
        <div className="stat card"><div className="stat-label">Open batches</div><div className="stat-value">{all.reduce((s, r) => s + r.open_batches, 0)}</div></div>
      </div>
      <Card pad={false}>
        <div className="card-body" style={{ paddingBottom: 0 }}><div className="toolbar">
          <input type="search" placeholder="Search stock…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search stock" />
          <label className="check"><input type="checkbox" checked={filter === "low"} onChange={(e) => setParams(e.target.checked ? { tab: "levels", filter: "low" } : { tab: "levels" })} /> Low stock only</label></div></div>
        {rows.length === 0 ? <Empty title="Nothing to show" /> : (
          <div className="table-wrap"><table><thead><tr><th>Ingredient</th><th className="num">On hand</th><th className="num hide-sm">Minimum</th><th className="num">Value</th><th className="num hide-sm">Batches</th><th>Status</th></tr></thead>
            <tbody>{rows.slice(0, shown).map((r) => (
              <tr key={r.ingredient_id} className={`clickable ${r.is_out && num(r.min_stock_base) > 0 ? "out" : r.is_low ? "low" : ""}`} onClick={() => nav(`/ingredients/${r.ingredient_id}`)}>
                <td><Link to={`/ingredients/${r.ingredient_id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link><div className="muted small">{r.category || ""}</div></td>
                <td className="num"><strong>{qty(r.on_hand, r.display_unit)}</strong></td><td className="num hide-sm">{num(r.min_stock_base) > 0 ? qty(r.min_stock, r.display_unit) : "—"}</td>
                <td className="num">{money(r.inventory_value)}</td><td className="num hide-sm">{r.open_batches}</td>
                <td>{r.is_out ? <span className="badge bad">out of stock</span> : r.is_low ? <span className="badge warn">low</span> : <span className="badge good">ok</span>}</td></tr>))}</tbody>
            <tfoot><tr><td>Total ({rows.length})</td><td /><td className="hide-sm" /><td className="num">{money(rows.reduce((s, r) => s + num(r.inventory_value), 0))}</td><td className="hide-sm" /><td /></tr></tfoot>
          </table></div>)}
        {rows.length > shown && (
          <div className="card-body row between"><span className="muted small">Showing {shown} of {rows.length}. Search or tick "Low stock only" to narrow down.</span>
            <Button variant="secondary" className="sm" onClick={() => setShown(shown + 200)}>Show more</Button></div>)}
      </Card>
    </>
  );
}

type QL = { ingredient_id: string; quantity: string; unit: string };

function ResultCard({ result, title }: { result: any; title: string }) {
  if (!result) return null;
  const lines = result.lines || [result];
  return (
    <Card title={title} actions={<BasisTag basis="fifo" />}>
      <ul className="list-plain" style={{ margin: "-16px" }}>{lines.map((l: any) => (
        <li key={l.movement_id}><span><strong>{l.ingredient_name}</strong> — {smartQty(l.quantity_base, l.base_unit)}
          <div className="muted small">{l.allocations.map((a: any) => `${smartQty(a.quantity, l.base_unit)} from batch = ${money(a.cost)}`).join(" · ")}</div>
          {num(l.shortfall_quantity) > 0 && <span className="badge warn">shortfall {smartQty(l.shortfall_quantity, l.base_unit)}</span>}</span>
          <strong className="num">{money(l.total_cost)}</strong></li>))}</ul>
    </Card>
  );
}

function OverrideBox({ error, value, onChange }: { error: unknown; value: { on: boolean; reason: string }; onChange: (v: { on: boolean; reason: string }) => void }) {
  const { can } = useAuth();
  if ((error as any)?.code !== "INSUFFICIENT_STOCK") return null;
  return (
    <div className="alert warn">
      Not enough stock recorded. {can("stock.override") ? (
        <div className="stack" style={{ marginTop: 8 }}>
          <label className="check"><input type="checkbox" checked={value.on} onChange={(e) => onChange({ ...value, on: e.target.checked })} /> Record anyway as an authorised shortfall (to be resolved at the next stock count)</label>
          {value.on && <input placeholder="Reason for override (required)" value={value.reason} onChange={(e) => onChange({ ...value, reason: e.target.value })} />}
        </div>) : "Ask a manager to record a delivery or authorise an override."}
    </div>
  );
}

function Usage() {
  const ings = useIngredients();
  const qc = useQueryClient();
  const toast = useToast();
  const byId = Object.fromEntries((ings.data || []).map((i) => [i.id, i])) as Record<string, Ingredient>;
  const [d, setD, clear] = useDraft<{ key: string; note: string; lines: QL[] }>("usage-draft", { key: newKey(), note: "", lines: [{ ingredient_id: "", quantity: "", unit: "g" }] });
  const [ov, setOv] = useState({ on: false, reason: "" });
  const [result, setResult] = useState<any>(null);
  const m = useMutation({
    mutationFn: () => post("/stock/consumptions", { note: d.note || null, lines: d.lines.filter((l) => l.ingredient_id && num(l.quantity) > 0),
      allow_shortfall: ov.on, override_reason: ov.on ? ov.reason : null }, d.key + (ov.on ? "-ov" : "")),
    onSuccess: (r: any) => { setResult(r); clear(); setD({ key: newKey(), note: "", lines: [{ ingredient_id: "", quantity: "", unit: "g" }] }); setOv({ on: false, reason: "" });
      qc.invalidateQueries(); toast("good", `Usage recorded — FIFO cost ${money(r.total_cost)}`); },
  });
  return (
    <div className="grid cols-2">
      <Card title="Record ingredient usage">
        <p className="muted small" style={{ marginTop: 0 }}>For ingredients used outside a recipe (e.g. ad-hoc prep). For recipe batches use <Link to="/stock?tab=produce">Production</Link> so ingredients are deducted from the recipe automatically.</p>
        {m.error instanceof NetworkError ? <div className="alert warn">{(m.error as Error).message} Your entry is kept on this device; nothing was posted.</div> : <ErrorBox error={m.error} />}
        <OverrideBox error={m.error} value={ov} onChange={setOv} />
        <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <QtyLines lines={d.lines} onChange={(lines) => setD({ ...d, lines })} ings={ings.data || []} byId={byId} />
          <div style={{ marginTop: 12 }}><Field label="Note">{(i) => <input id={i} value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} placeholder="e.g. Staff lunch prep" />}</Field></div>
          <div className="form-actions"><Button type="submit" busy={m.isPending} disabled={!d.lines.some((l) => l.ingredient_id && num(l.quantity) > 0)}>Deduct from stock</Button></div>
        </form>
      </Card>
      <ResultCard result={result} title={result ? `Recorded — total ${money(result.total_cost)}` : ""} />
    </div>
  );
}

function QtyLines({ lines, onChange, ings, byId }: { lines: QL[]; onChange: (l: QL[]) => void; ings: Ingredient[]; byId: Record<string, Ingredient> }) {
  const setLine = (i: number, p: Partial<QL>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  return (
    <>
      {lines.map((l, i) => (
        <div key={i} className="row" style={{ marginBottom: 8, flexWrap: "nowrap" }}>
          <div style={{ flex: 1, minWidth: 0 }}><SearchSelect items={ings.filter((x) => x.is_active)} value={l.ingredient_id} label={(x) => x.name} placeholder="Ingredient…"
            onChange={(v) => setLine(i, { ingredient_id: v, unit: byId[v]?.consumption_unit || "g" })} /></div>
          <input type="number" step="any" min="0" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} aria-label="Quantity" style={{ width: 100 }} />
          <select value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} aria-label="Unit" style={{ width: 80 }}>{unitsFor(byId[l.ingredient_id]).map((u) => <option key={u}>{u}</option>)}</select>
          {lines.length > 1 && <button type="button" className="icon-btn" aria-label="Remove" onClick={() => onChange(lines.filter((_, j) => j !== i))}>×</button>}
        </div>))}
      <Button type="button" variant="secondary" className="sm" onClick={() => onChange([...lines, { ingredient_id: "", quantity: "", unit: "g" }])}>+ Add ingredient</Button>
    </>
  );
}

function Production() {
  const [params] = useSearchParams();
  const recipes = useQuery({ queryKey: ["recipes"], queryFn: () => get<any[]>("/recipes") });
  const qc = useQueryClient();
  const toast = useToast();
  const published = (recipes.data || []).filter((r) => r.published_version_no);
  const [f, setF] = useState({ recipe_id: params.get("recipe") || "", mode: "portions", amount: "", actual: "", note: "" });
  const [key, setKey] = useState(newKey);
  const [ov, setOv] = useState({ on: false, reason: "" });
  const [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState<any>(null);
  const recipe = published.find((r) => r.id === f.recipe_id);
  useEffect(() => { if (recipe && !f.amount) setF((x) => ({ ...x, amount: String(Number(recipe.portions_per_batch)) })); }, [recipe]); // eslint-disable-line
  const req = useQuery({
    queryKey: ["requirements", f.recipe_id, f.mode, f.amount],
    queryFn: () => get(`/stock/production/requirements?recipe_id=${f.recipe_id}&${f.mode === "portions" ? "portions" : "batches"}=${f.amount}`),
    enabled: !!f.recipe_id && num(f.amount) > 0, retry: false,
  });
  const m = useMutation({
    mutationFn: () => post("/stock/production-runs", { recipe_id: f.recipe_id, [f.mode]: f.amount, actual_portions: f.actual || null, note: f.note || null,
      allow_shortfall: ov.on, override_reason: ov.on ? ov.reason : null }, key + (ov.on ? "-ov" : "")),
    onSuccess: (r: any) => { setResult(r); setConfirm(false); setKey(newKey()); setOv({ on: false, reason: "" }); qc.invalidateQueries();
      toast("good", `Production recorded — ingredients deducted, FIFO cost ${money(r.total_cost)}`); },
    onError: () => setConfirm(false),
  });
  const insufficient = req.data?.lines.some((l: any) => !l.sufficient);
  return (
    <div className="grid cols-2">
      <Card title="Record production from a recipe">
        <p className="muted small" style={{ marginTop: 0 }}>Ingredients are deducted once, here, using the approved recipe version. Selling the dishes later will not deduct them again.</p>
        {m.error instanceof NetworkError ? <div className="alert warn">{(m.error as Error).message} Nothing was posted. Retrying is safe.</div> : <ErrorBox error={m.error} />}
        <OverrideBox error={m.error} value={ov} onChange={setOv} />
        <form onSubmit={(e) => { e.preventDefault(); setConfirm(true); }}>
          <div className="form-grid">
            <Field label="Recipe">{(i) => <SearchSelect id={i} items={published} value={f.recipe_id} label={(r) => `${r.name} (v${r.published_version_no})`} onChange={(v) => setF({ ...f, recipe_id: v, amount: "" })} placeholder="Choose recipe…" />}</Field>
            <Field label="Prepared">{(i) => <div className="row" style={{ flexWrap: "nowrap" }}><input id={i} type="number" step="any" min="0" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
              <select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} aria-label="Portions or batches" style={{ width: 120 }}><option value="portions">portions</option><option value="batches">batches</option></select></div>}</Field>
            <Field label="Actual portions (optional)" hint="If the yield differed">{(i) => <input id={i} type="number" step="any" min="0" value={f.actual} onChange={(e) => setF({ ...f, actual: e.target.value })} />}</Field>
          </div>
          <div style={{ marginTop: 12 }}><Field label="Note">{(i) => <input id={i} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />}</Field></div>
          {req.data && (
            <div className="table-wrap" style={{ marginTop: 14 }}><table><thead><tr><th>Will deduct</th><th className="num">Needed</th><th className="num">In stock</th></tr></thead>
              <tbody>{req.data.lines.map((l: any) => (<tr key={l.ingredient_id}><td>{l.name}</td><td className="num">{qty(l.required, l.unit)}</td>
                <td className="num">{l.sufficient ? smartQty(l.on_hand_base, l.base_unit) : <span className="badge bad">{smartQty(l.on_hand_base, l.base_unit)} — short</span>}</td></tr>))}</tbody></table></div>)}
          <ErrorBox error={req.error} />
          <div className="form-actions"><Button type="submit" disabled={!f.recipe_id || !(num(f.amount) > 0)}>Record production</Button></div>
        </form>
      </Card>
      {result && (
        <Card title="Production recorded" actions={<BasisTag basis="fifo" />}>
          <dl className="kv"><dt>Recipe version</dt><dd>v{result.recipe_version_no}</dd><dt>Portions</dt><dd>{qty(result.actual_portions ?? result.expected_portions)}</dd>
            <dt>Actual ingredient cost</dt><dd><strong>{money(result.total_cost)}</strong></dd><dt>Per portion</dt><dd>{money(result.cost_per_portion)}</dd></dl>
          <p className="muted small">Cost comes from the actual batches used (FIFO), which can differ from the recipe's current-price cost.</p>
        </Card>)}
      {confirm && (
        <Confirm title="Deduct ingredients for this production?" confirmLabel="Deduct & record" busy={m.isPending} onClose={() => setConfirm(false)} onConfirm={() => m.mutate()}>
          <p>{recipe?.name}: {qty(f.amount)} {f.mode}. {insufficient && !ov.on && <strong>Some ingredients are short — this will be blocked unless an override is authorised.</strong>}</p>
        </Confirm>)}
    </div>
  );
}

function Waste() {
  const ings = useIngredients();
  const qc = useQueryClient();
  const toast = useToast();
  const byId = Object.fromEntries((ings.data || []).map((i) => [i.id, i])) as Record<string, Ingredient>;
  const blank = () => ({ key: newKey(), ingredient_id: "", quantity: "", unit: "g", reason: "spoilage", note: "" });
  const [f, setF] = useState(blank);
  const [result, setResult] = useState<any>(null);
  const m = useMutation({
    mutationFn: () => post("/stock/adjustments", { ingredient_id: f.ingredient_id, quantity: f.quantity, unit: f.unit, reason: f.reason, note: f.note || null }, f.key),
    onSuccess: (r: any) => { setResult(r); setF(blank()); qc.invalidateQueries(); toast("good", `Waste recorded — ${money(r.total_cost)}`); },
  });
  return (
    <div className="grid cols-2">
      <Card title="Record waste or spoilage">
        <ErrorBox error={m.error} />
        <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <div className="form-grid">
            <Field label="Ingredient">{(i) => <SearchSelect id={i} items={(ings.data || []).filter((x) => x.is_active)} value={f.ingredient_id} label={(x) => x.name} onChange={(v) => setF({ ...f, ingredient_id: v, unit: byId[v]?.consumption_unit || "g" })} />}</Field>
            <Field label="Quantity">{(i) => <div className="row" style={{ flexWrap: "nowrap" }}><input id={i} type="number" step="any" min="0" required value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />
              <select value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} style={{ width: 80 }} aria-label="Unit">{unitsFor(byId[f.ingredient_id]).map((u) => <option key={u}>{u}</option>)}</select></div>}</Field>
            <Field label="Reason">{(i) => <select id={i} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}>
              <option value="spoilage">Spoilage</option><option value="expired">Expired</option><option value="damaged">Damaged</option><option value="prep_waste">Preparation waste</option><option value="staff_meal">Staff consumption</option><option value="other">Other (describe)</option></select>}</Field>
          </div>
          <div style={{ marginTop: 12 }}><Field label={f.reason === "other" ? "Description (required)" : "Note"}>{(i) => <input id={i} required={f.reason === "other"} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. Fridge 2 failed overnight" />}</Field></div>
          <div className="form-actions"><Button type="submit" busy={m.isPending} disabled={!f.ingredient_id}>Record waste</Button></div>
        </form>
      </Card>
      <ResultCard result={result} title={result ? `Waste recorded — ${money(result.total_cost)}` : ""} />
    </div>
  );
}

function Counts() {
  const { can } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const q = useQuery({ queryKey: ["counts"], queryFn: () => get<any[]>("/stock/counts") });
  const start = useMutation({ mutationFn: () => post("/stock/counts", { note: `Count ${new Date().toLocaleDateString()}` }),
    onSuccess: (c: any) => { toast("info", "Stock count started"); nav(`/stock/counts/${c.id}`); } });
  return (
    <Card pad={false} title="Physical stock counts" actions={can("stock.count") && <Button onClick={() => start.mutate()} busy={start.isPending}>Start new count</Button>}>
      <ErrorBox error={start.error} />
      {q.isLoading ? <Loading /> : !q.data?.length ? <Empty title="No stock counts yet">Count what is physically on the shelves and compare it with the system.</Empty> : (
        <div className="table-wrap"><table><thead><tr><th>Started</th><th>Note</th><th className="num">Counted</th><th>Status</th></tr></thead>
          <tbody>{q.data.map((c) => (<tr key={c.id} className="clickable" onClick={() => nav(`/stock/counts/${c.id}`)}><td>{dateTime(c.started_at)}</td><td>{c.note}</td>
            <td className="num">{c.counted} / {c.lines}</td><td><StatusBadge status={c.status} /></td></tr>))}</tbody></table></div>)}
    </Card>
  );
}

export function CountSheet() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["count", id], queryFn: () => get(`/stock/counts/${id}`) });
  const [entries, setEntries, clear] = useDraft<Record<string, string>>(`count-${id}`, {});
  const [confirm, setConfirm] = useState(false);
  const [key] = useState(newKey);
  const save = useMutation({
    mutationFn: () => put(`/stock/counts/${id}/lines`, { lines: q.data.lines.filter((l: any) => entries[l.ingredient_id] !== undefined)
      .map((l: any) => ({ ingredient_id: l.ingredient_id, quantity: entries[l.ingredient_id] === "" ? null : entries[l.ingredient_id], unit: l.unit })) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["count", id] }); toast("good", "Counts saved"); },
  });
  const approve = useMutation({
    mutationFn: async () => { await save.mutateAsync(); return post(`/stock/counts/${id}/approve`, {}, key); },
    onSuccess: () => { clear(); qc.invalidateQueries(); setConfirm(false); toast("good", "Count approved — stock adjusted to the counted quantities"); },
    onError: () => setConfirm(false),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const c = q.data;
  const isOpen = c.status === "open";
  const val = (l: any) => entries[l.ingredient_id] ?? (l.counted === null ? "" : String(Number(l.counted)));
  const variances = c.lines.filter((l: any) => val(l) !== "" && Math.abs(num(val(l)) - num(l.system_quantity)) > 1e-9);
  return (
    <Page title="Stock count" subtitle={<>{c.note} · started {dateTime(c.started_at)} · <StatusBadge status={c.status} /></>}
      actions={isOpen && <>
        {can("stock.count") && <Button variant="secondary" busy={save.isPending} onClick={() => save.mutate()}>Save counts</Button>}
        {can("stock.count.approve") && <Button onClick={() => setConfirm(true)} disabled={!c.lines.some((l: any) => val(l) !== "")}>Review & approve</Button>}
      </>}>
      <ErrorBox error={save.error || approve.error} />
      {isOpen && <div className="alert info">Enter what is physically on the shelf. Leave blank to skip an item. Your entries are saved on this device as you type. Variances are calculated against the system quantity when the count is approved.</div>}
      <Card pad={false}>
        <div className="table-wrap"><table className="line-table"><thead><tr><th>Ingredient</th><th className="num">System quantity</th><th style={{ width: 160 }}>Counted</th><th className="num">Difference</th>{!isOpen && <th className="num">Value</th>}</tr></thead>
          <tbody>{c.lines.map((l: any) => {
            const v = val(l);
            const diff = v === "" ? null : num(v) - num(l.system_quantity);
            return (<tr key={l.ingredient_id}><td>{l.name}</td><td className="num">{qty(l.system_quantity, l.unit)}</td>
              <td>{isOpen ? <div className="row" style={{ flexWrap: "nowrap" }}><input type="number" step="any" min="0" value={v} aria-label={`Counted ${l.name}`}
                onChange={(e) => setEntries({ ...entries, [l.ingredient_id]: e.target.value })} /><span className="muted small">{l.unit}</span></div> : qty(l.counted, l.unit)}</td>
              <td className="num">{diff === null ? "—" : <span style={{ color: diff < 0 ? "var(--bad)" : diff > 0 ? "var(--good)" : undefined }}>{diff > 0 ? "+" : ""}{qty(diff, l.unit)}</span>}</td>
              {!isOpen && <td className="num">{money(l.variance_value)}</td>}</tr>);
          })}</tbody>
          {!isOpen && <tfoot><tr><td colSpan={4}>Net variance value</td><td className="num">{money(c.lines.reduce((s: number, l: any) => s + num(l.variance_value || 0), 0))}</td></tr></tfoot>}</table></div>
      </Card>
      {confirm && (
        <Confirm title="Approve this stock count?" confirmLabel="Approve & adjust stock" busy={approve.isPending} onClose={() => setConfirm(false)} onConfirm={() => approve.mutate()}>
          <p>{variances.length} item(s) differ from the system. Shortages are removed from the oldest batches (FIFO-costed); surpluses are added at the current cost. This is recorded in the audit log.</p>
          <ul>{variances.slice(0, 12).map((l: any) => <li key={l.ingredient_id}>{l.name}: {qty(num(val(l)) - num(l.system_quantity), l.unit)}</li>)}</ul>
        </Confirm>)}
    </Page>
  );
}

function History() {
  const [type, setType] = useState("");
  const q = useQuery({ queryKey: ["movements", "all", type], queryFn: () => get(`/stock/movements?limit=200${type ? `&type=${type}` : ""}`) });
  return (
    <>
      <div className="toolbar"><select value={type} onChange={(e) => setType(e.target.value)} aria-label="Movement type">
        <option value="">All movements</option><option value="receipt">Purchases received</option><option value="consumption">Usage</option><option value="production">Production</option>
        <option value="adjustment">Waste</option><option value="count_adjustment">Stock counts</option><option value="purchase_void">Voided purchases</option></select></div>
      <MovementTable items={q.data?.items} loading={q.isLoading} showIngredient />
    </>
  );
}
