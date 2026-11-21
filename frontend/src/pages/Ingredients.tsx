import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  BasisTag, Button, Card, Empty, ErrorBox, Field, LinkButton, Loading, Modal, Page, StatusBadge, Tabs, useToast,
} from "../components/ui";
import { get, patch, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { DIMENSION_UNITS, UNITS, date, dateTime, money, pct, qty, smartQty, today, unitCost } from "../lib/format";
import type { Ingredient, Supplier } from "../lib/types";
import { RecipesList } from "./Recipes";

export function useIngredients() {
  return useQuery({ queryKey: ["ingredients"], queryFn: () => get<{ items: Ingredient[] }>("/ingredients?limit=500"),
                    select: (d) => d.items, staleTime: 30_000 });
}

export function IngredientsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "ingredients";
  const { can } = useAuth();
  return (
    <Page title="Ingredients & Recipes" subtitle="What you buy, what it costs, and what your dishes cost to make."
      actions={tab === "recipes"
        ? can("recipe.write") && <LinkButton to="/recipes/new">New recipe</LinkButton>
        : can("ingredient.write") && <LinkButton to="/ingredients/new">New ingredient</LinkButton>}>
      <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={[{ id: "ingredients", label: "Ingredients" }, { id: "recipes", label: "Recipes" }]} />
      {tab === "recipes" ? <RecipesList /> : <IngredientList />}
    </Page>
  );
}

function IngredientList() {
  const q = useIngredients();
  const [search, setSearch] = useState("");
  const [cat, setCat] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const nav = useNavigate();
  const items = q.data || [];
  const cats = useMemo(() => [...new Set(items.map((i) => i.category).filter(Boolean))].sort() as string[], [items]);
  const filtered = items.filter((i) => (showInactive || i.is_active) && (!cat || i.category === cat)
    && (!search || `${i.name} ${i.code}`.toLowerCase().includes(search.toLowerCase())));
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <Card pad={false}>
      <div className="card-body" style={{ paddingBottom: 0 }}>
        <div className="toolbar">
          <input type="search" placeholder="Search ingredients…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search ingredients" />
          <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category"><option value="">All categories</option>{cats.map((c) => <option key={c}>{c}</option>)}</select>
          <label className="check"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show inactive</label>
          <span className="muted small" style={{ marginLeft: "auto" }}>{filtered.length} of {items.length}</span>
        </div>
      </div>
      {filtered.length === 0 ? <Empty title={items.length ? "No ingredients match" : "No ingredients yet"}>Add the ingredients you buy to start costing recipes.</Empty> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Ingredient</th><th className="hide-sm">Category</th><th className="hide-sm">Buy / use in</th><th className="num">Current cost</th><th className="hide-sm">Since</th><th>Status</th></tr></thead>
          <tbody>{filtered.map((i) => (
            <tr key={i.id} className="clickable" onClick={() => nav(`/ingredients/${i.id}`)}>
              <td><Link to={`/ingredients/${i.id}`} onClick={(e) => e.stopPropagation()}>{i.name}</Link><div className="muted small mono">{i.code}</div></td>
              <td className="hide-sm">{i.category || "—"}</td>
              <td className="hide-sm">{i.purchase_unit} / {i.consumption_unit}</td>
              <td className="num">{i.current_cost ? unitCost(perPurchaseUnit(i), i.purchase_unit) : <span className="badge warn">no price</span>}</td>
              <td className="hide-sm">{date(i.current_cost?.effective_date)}</td>
              <td>{i.is_active ? <StatusBadge status="confirmed" /> : <span className="badge neutral">inactive</span>}</td>
            </tr>))}</tbody>
        </table></div>)}
    </Card>
  );
}

const FACTORS: Record<string, number> = { g: 1, kg: 1000, ml: 1, l: 1000, unit: 1 };
const DIM: Record<string, string> = { g: "mass", kg: "mass", ml: "volume", l: "volume", unit: "count" };

/** Display helper: current cost expressed per purchase unit (server stores per base unit). */
export function perPurchaseUnit(i: Ingredient, unit = i.purchase_unit): number {
  const cpb = Number(i.current_cost?.cost_per_base_unit ?? NaN);
  return cpb * toBase(i, 1, unit);
}
export function toBase(i: Ingredient, q: number, unit: string): number {
  if (DIM[unit] === i.base_dimension) return q * FACTORS[unit];
  for (const c of i.conversions) {
    if (DIM[c.from_unit] === DIM[unit] && DIM[c.to_unit] === i.base_dimension)
      return q * FACTORS[unit] / (Number(c.from_qty) * FACTORS[c.from_unit]) * Number(c.to_qty) * FACTORS[c.to_unit];
    if (DIM[c.to_unit] === DIM[unit] && DIM[c.from_unit] === i.base_dimension)
      return q * FACTORS[unit] / (Number(c.to_qty) * FACTORS[c.to_unit]) * Number(c.from_qty) * FACTORS[c.from_unit];
  }
  return NaN;
}
export function unitsFor(i?: Ingredient): string[] {
  if (!i) return [...UNITS];
  const set = new Set(DIMENSION_UNITS[i.base_dimension]);
  for (const c of i.conversions) for (const u of [c.from_unit, c.to_unit]) DIMENSION_UNITS[DIM[u]].forEach((x) => set.add(x));
  return [...set];
}

type Conv = { from_unit: string; from_qty: string; to_unit: string; to_qty: string; note?: string };

export function IngredientForm() {
  const { id } = useParams();
  const editing = !!id;
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const existing = useQuery({ queryKey: ["ingredient", id], queryFn: () => get<Ingredient>(`/ingredients/${id}`), enabled: editing });
  const suppliers = useQuery({ queryKey: ["suppliers"], queryFn: () => get<Supplier[]>("/suppliers") });
  const [f, setF] = useState<any>(null);
  const init = editing ? existing.data : null;
  const form = f ?? (init ? {
    code: init.code, name: init.name, category: init.category || "", description: init.description || "",
    base_dimension: init.base_dimension, purchase_unit: init.purchase_unit, consumption_unit: init.consumption_unit,
    is_active: init.is_active, min_stock: init.min_stock_base === "0" ? "" : String(Number(init.min_stock_base)), min_stock_unit: init.base_unit,
    conversions: init.conversions.map((c) => ({ ...c, from_qty: String(Number(c.from_qty)), to_qty: String(Number(c.to_qty)) })),
    suppliers: init.suppliers.map((s) => s.supplier_id), price_qty: "", price_amount: "",
  } : {
    code: "", name: "", category: "", description: "", base_dimension: "mass", purchase_unit: "kg", consumption_unit: "g",
    is_active: true, min_stock: "", min_stock_unit: "kg", conversions: [] as Conv[], suppliers: [] as string[], price_qty: "", price_amount: "",
  });
  const set = (k: string, v: any) => setF({ ...form, [k]: v });
  const dims = DIMENSION_UNITS[form.base_dimension];
  const allowed = new Set([...dims, ...form.conversions.flatMap((c: Conv) => [c.from_unit, c.to_unit]).flatMap((u: string) => DIMENSION_UNITS[DIM[u]])]);

  const m = useMutation({
    mutationFn: async () => {
      const body: any = {
        code: form.code, name: form.name, category: form.category || null, description: form.description || null,
        base_dimension: form.base_dimension, purchase_unit: form.purchase_unit, consumption_unit: form.consumption_unit,
        is_active: form.is_active, min_stock: form.min_stock === "" ? "0" : form.min_stock, min_stock_unit: form.min_stock_unit,
        conversions: form.conversions.filter((c: Conv) => c.from_qty && c.to_qty),
        suppliers: form.suppliers.map((s: string) => ({ supplier_id: s })),
      };
      if (editing) return patch<Ingredient>(`/ingredients/${id}`, body, { "If-Match": String(init!.version) });
      if (form.price_qty && form.price_amount)
        body.reference_price = { quantity: form.price_qty, unit: form.purchase_unit, amount: form.price_amount, effective_date: today() };
      return post<Ingredient>("/ingredients", body);
    },
    onSuccess: (ing) => {
      qc.invalidateQueries({ queryKey: ["ingredients"] }); qc.invalidateQueries({ queryKey: ["ingredient", ing.id] });
      toast("good", editing ? `${ing.name} updated` : `${ing.name} created`);
      nav(`/ingredients/${ing.id}`);
    },
  });

  if (editing && existing.isLoading) return <Loading />;
  const sameDim = DIM[form.purchase_unit] === form.base_dimension;
  return (
    <Page title={editing ? `Edit ${init?.name}` : "New ingredient"} subtitle="Stock is tracked in the ingredient's base unit (grams, millilitres or units).">
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }} className="stack">
        <ErrorBox error={m.error} />
        <Card title="Details">
          <div className="form-grid">
            <Field label="Name">{(i) => <input id={i} required value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Beef mince" />}</Field>
            <Field label="Code" hint="Short unique code, e.g. BEEF-MINCE">{(i) => <input id={i} required pattern="[A-Za-z0-9_\-]+" value={form.code} onChange={(e) => set("code", e.target.value.toUpperCase())} />}</Field>
            <Field label="Category">{(i) => <input id={i} value={form.category} onChange={(e) => set("category", e.target.value)} placeholder="Meat, Dry goods, Dairy…" list="cats" />}</Field>
            <Field label="Status">{(i) => <select id={i} value={form.is_active ? "1" : "0"} onChange={(e) => set("is_active", e.target.value === "1")}><option value="1">Active</option><option value="0">Inactive</option></select>}</Field>
          </div>
          <div style={{ marginTop: 14 }}><Field label="Description">{(i) => <textarea id={i} value={form.description} onChange={(e) => set("description", e.target.value)} />}</Field></div>
        </Card>
        <Card title="Units">
          <div className="form-grid">
            <Field label="Measured by" hint="How stock is counted">{(i) => (
              <select id={i} value={form.base_dimension} disabled={editing && Number(init?.on_hand_base) > 0} onChange={(e) => {
                const d = e.target.value; const u = DIMENSION_UNITS[d];
                setF({ ...form, base_dimension: d, purchase_unit: u[u.length - 1], consumption_unit: u[0], min_stock_unit: u[u.length - 1] });
              }}><option value="mass">Weight (g, kg)</option><option value="volume">Volume (ml, l)</option><option value="count">Count (units)</option></select>)}</Field>
            <Field label="Purchase unit">{(i) => <select id={i} value={form.purchase_unit} onChange={(e) => set("purchase_unit", e.target.value)}>{[...UNITS].filter((u) => allowed.has(u)).map((u) => <option key={u}>{u}</option>)}</select>}</Field>
            <Field label="Consumption unit" hint="Unit used in recipes and stock counts">{(i) => <select id={i} value={form.consumption_unit} onChange={(e) => set("consumption_unit", e.target.value)}>{[...UNITS].filter((u) => allowed.has(u)).map((u) => <option key={u}>{u}</option>)}</select>}</Field>
            <Field label="Minimum stock level">{(i) => (
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <input id={i} type="number" min="0" step="any" value={form.min_stock} onChange={(e) => set("min_stock", e.target.value)} placeholder="0" />
                <select value={form.min_stock_unit} onChange={(e) => set("min_stock_unit", e.target.value)} style={{ width: 90 }} aria-label="Minimum stock unit">{dims.map((u: string) => <option key={u}>{u}</option>)}</select>
              </div>)}</Field>
          </div>
          <h3 style={{ marginTop: 18 }}>Conversion rules</h3>
          <p className="muted small" style={{ marginTop: 0 }}>Only needed when you buy or use this ingredient in a different kind of unit, e.g. oil bought in litres but tracked by weight (1 l = 910 g), or a sack counted as 1 unit = 25 kg. The system never guesses a density.</p>
          {form.conversions.map((c: Conv, idx: number) => (
            <div className="row" key={idx} style={{ marginBottom: 8, flexWrap: "nowrap" }}>
              <input type="number" step="any" min="0" value={c.from_qty} aria-label="From quantity" style={{ maxWidth: 100 }}
                onChange={(e) => set("conversions", form.conversions.map((x: Conv, j: number) => j === idx ? { ...x, from_qty: e.target.value } : x))} />
              <select value={c.from_unit} aria-label="From unit" style={{ maxWidth: 90 }} onChange={(e) => set("conversions", form.conversions.map((x: Conv, j: number) => j === idx ? { ...x, from_unit: e.target.value } : x))}>{UNITS.map((u) => <option key={u}>{u}</option>)}</select>
              <span>=</span>
              <input type="number" step="any" min="0" value={c.to_qty} aria-label="To quantity" style={{ maxWidth: 110 }}
                onChange={(e) => set("conversions", form.conversions.map((x: Conv, j: number) => j === idx ? { ...x, to_qty: e.target.value } : x))} />
              <select value={c.to_unit} aria-label="To unit" style={{ maxWidth: 90 }} onChange={(e) => set("conversions", form.conversions.map((x: Conv, j: number) => j === idx ? { ...x, to_unit: e.target.value } : x))}>{UNITS.map((u) => <option key={u}>{u}</option>)}</select>
              <button type="button" className="icon-btn" aria-label="Remove rule" onClick={() => set("conversions", form.conversions.filter((_: Conv, j: number) => j !== idx))}>×</button>
            </div>))}
          <Button type="button" variant="secondary" className="sm" onClick={() => set("conversions", [...form.conversions, { from_unit: form.base_dimension === "mass" ? "l" : "kg", from_qty: "1", to_unit: dims[0], to_qty: "" }])}>+ Add conversion rule</Button>
        </Card>
        <Card title="Suppliers & price">
          <Field label="Suppliers who sell this">{() => (
            <div className="row">{(suppliers.data || []).map((s) => (
              <label key={s.id} className="check"><input type="checkbox" checked={form.suppliers.includes(s.id)}
                onChange={(e) => set("suppliers", e.target.checked ? [...form.suppliers, s.id] : form.suppliers.filter((x: string) => x !== s.id))} /> {s.name}</label>))}
              {suppliers.data?.length === 0 && <span className="muted small">No suppliers yet — <Link to="/suppliers">add one</Link>.</span>}</div>)}</Field>
          {!editing && (
            <div className="form-grid" style={{ marginTop: 14 }}>
              <Field label={`Starting price: quantity (${form.purchase_unit})`} hint="Optional — e.g. a supplier quote. Real purchases update this automatically.">
                {(i) => <input id={i} type="number" step="any" min="0" value={form.price_qty} onChange={(e) => set("price_qty", e.target.value)} placeholder="25" />}</Field>
              <Field label="…for a total price of" error={form.price_qty && form.price_amount && !sameDim && Number.isNaN(toBase({ ...form, conversions: form.conversions } as any, 1, form.purchase_unit)) ? "Add a conversion rule for this purchase unit" : undefined}>
                {(i) => <input id={i} type="number" step="any" min="0" value={form.price_amount} onChange={(e) => set("price_amount", e.target.value)} placeholder="50.00" />}</Field>
            </div>)}
        </Card>
        <div className="form-actions">
          <Button type="button" variant="secondary" onClick={() => nav(-1)}>Cancel</Button>
          <Button type="submit" busy={m.isPending}>{editing ? "Save changes" : "Create ingredient"}</Button>
        </div>
      </form>
    </Page>
  );
}

export function IngredientDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const [tab, setTab] = useState("prices");
  const [priceOpen, setPriceOpen] = useState(false);
  const q = useQuery({ queryKey: ["ingredient", id], queryFn: () => get<Ingredient>(`/ingredients/${id}`) });
  const hist = useQuery({ queryKey: ["price-history", id], queryFn: () => get<any[]>(`/ingredients/${id}/price-history`) });
  const batches = useQuery({ queryKey: ["batches", id], queryFn: () => get(`/stock/ingredients/${id}/batches`), enabled: can("stock.read") });
  const moves = useQuery({ queryKey: ["movements", id], queryFn: () => get(`/stock/movements?ingredient_id=${id}&limit=50`), enabled: tab === "moves" });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const i = q.data!;
  const value = (batches.data?.batches || []).reduce((s: number, b: any) => s + Number(b.remaining_value), 0);
  return (
    <Page title={i.name} subtitle={<><span className="mono">{i.code}</span> · {i.category || "Uncategorised"} {!i.is_active && <span className="badge neutral">inactive</span>}</>}
      actions={<>
        {can("ingredient.write") && <Button variant="secondary" onClick={() => setPriceOpen(true)}>Record quoted price</Button>}
        {can("ingredient.write") && <LinkButton to={`/ingredients/${i.id}/edit`}>Edit</LinkButton>}
      </>}>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="stat card"><div className="stat-label">Current cost</div>
          <div className="stat-value">{i.current_cost ? money(perPurchaseUnit(i)) : "—"}</div>
          <div className="stat-hint">{i.current_cost ? <>per {i.purchase_unit} · {unitCost(i.current_cost.cost_per_base_unit, i.base_unit)}</> : "No price recorded yet"}</div></div>
        <div className="stat card"><div className="stat-label">On hand</div>
          <div className={`stat-value ${Number(i.on_hand_base) < Number(i.min_stock_base) ? "bad" : ""}`}>{smartQty(i.on_hand_base, i.base_unit)}</div>
          <div className="stat-hint">Minimum {smartQty(i.min_stock_base, i.base_unit)}</div></div>
        <div className="stat card"><div className="stat-label">Stock value</div><div className="stat-value">{money(value)}</div><div className="stat-hint"><BasisTag basis="fifo" /></div></div>
        <div className="stat card"><div className="stat-label">Units</div><div className="stat-value" style={{ fontSize: "1.1rem" }}>Buy in {i.purchase_unit} · use in {i.consumption_unit}</div>
          <div className="stat-hint">{i.conversions.map((c) => `${qty(c.from_qty)} ${c.from_unit} = ${qty(c.to_qty)} ${c.to_unit}`).join(" · ") || `Tracked in ${i.base_unit}`}</div></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "prices", label: "Purchase price history" }, { id: "batches", label: `Stock batches (${batches.data?.batches.length ?? 0})` }, { id: "moves", label: "Movements" }, { id: "info", label: "Suppliers & details" }]} />
      {tab === "prices" && (
        <Card pad={false} title="Price history" actions={<BasisTag basis="current" />}>
          {!hist.data?.length ? <Empty title="No prices yet">Prices are recorded automatically when you confirm a purchase.</Empty> : (
            <div className="table-wrap"><table><thead><tr><th>Date</th><th>Source</th><th className="num">Quantity</th><th className="num">Amount</th><th className="num">Cost per {i.purchase_unit}</th><th className="num hide-sm">Change</th></tr></thead>
              <tbody>{hist.data.map((h) => {
                const per = Number(h.cost_per_base_unit) * toBase(i, 1, i.purchase_unit);
                const prev = h.previous_cost_per_base_unit ? Number(h.previous_cost_per_base_unit) : null;
                const ch = prev ? (Number(h.cost_per_base_unit) - prev) / prev : null;
                return (<tr key={h.id} style={h.is_voided ? { opacity: .5, textDecoration: "line-through" } : undefined}>
                  <td>{date(h.effective_date)}</td><td>{h.source === "purchase" ? h.supplier : "Quoted / manual"}{h.is_voided && " (voided)"}</td>
                  <td className="num">{h.purchase_unit === "base" ? smartQty(h.purchase_quantity, i.base_unit) : qty(h.purchase_quantity, h.purchase_unit)}</td>
                  <td className="num">{money(h.purchase_amount)}</td><td className="num"><strong>{money(per, { decimals: per < 1 ? 4 : undefined })}</strong></td>
                  <td className="num hide-sm">{ch === null || ch === 0 ? "—" : <span className={`badge ${ch > 0 ? "bad" : "good"}`}>{ch > 0 ? "▲" : "▼"} {pct(Math.abs(ch))}</span>}</td></tr>);
              })}</tbody></table></div>)}
        </Card>)}
      {tab === "batches" && <BatchTable data={batches.data} />}
      {tab === "moves" && <MovementTable items={moves.data?.items} loading={moves.isLoading} />}
      {tab === "info" && (
        <Card title="Details">
          <dl className="kv">
            <dt>Suppliers</dt><dd>{i.suppliers.length ? i.suppliers.map((s) => <Link key={s.supplier_id} to={`/suppliers/${s.supplier_id}`} style={{ marginRight: 10 }}>{s.supplier_name}{s.is_preferred ? " ★" : ""}</Link>) : "—"}</dd>
            <dt>Description</dt><dd>{i.description || "—"}</dd>
            <dt>Stock dimension</dt><dd>{i.base_dimension} (base unit {i.base_unit})</dd>
          </dl>
        </Card>)}
      {priceOpen && <PriceModal ing={i} onClose={() => setPriceOpen(false)} />}
    </Page>
  );
}

export function BatchTable({ data }: { data: any }) {
  if (!data) return <Loading />;
  const b = data.batches as any[];
  return (
    <Card pad={false} title="Open batches — used oldest first" actions={<BasisTag basis="fifo" />}>
      {b.length === 0 ? <Empty title="No stock on hand" /> : (
        <div className="table-wrap"><table><thead><tr><th>#</th><th>Received</th><th className="hide-sm">Supplier</th><th className="num">Received qty</th><th className="num">Remaining</th><th className="num">Unit cost</th><th className="num">Value</th></tr></thead>
          <tbody>{b.map((x) => (
            <tr key={x.id}><td>{x.fifo_position === 1 ? <span className="badge info">next</span> : x.fifo_position}</td>
              <td>{date(x.received_at)}{x.purchase_id && <div className="small"><Link to={`/purchases/${x.purchase_id}`}>invoice</Link></div>}</td>
              <td className="hide-sm">{x.supplier || (x.source_type === "stock_count" ? "Stock count" : "—")}</td>
              <td className="num">{smartQty(x.original_quantity, data.base_unit)}</td><td className="num"><strong>{smartQty(x.remaining_quantity, data.base_unit)}</strong></td>
              <td className="num">{unitCost(x.unit_cost, data.base_unit)}</td><td className="num">{money(x.remaining_value)}</td></tr>))}</tbody>
          <tfoot><tr><td colSpan={4}>Total</td><td className="num">{smartQty(b.reduce((s, x) => s + Number(x.remaining_quantity), 0), data.base_unit)}</td><td className="hide-sm" /><td className="num">{money(b.reduce((s, x) => s + Number(x.remaining_value), 0))}</td></tr></tfoot>
        </table></div>)}
    </Card>
  );
}

const MOVE_LABEL: Record<string, string> = { receipt: "Purchase received", consumption: "Used", production: "Production", adjustment: "Waste / adjustment",
  count_adjustment: "Stock count", purchase_void: "Purchase voided", sale: "Sale" };

export function MovementTable({ items, loading, showIngredient }: { items?: any[]; loading?: boolean; showIngredient?: boolean }) {
  if (loading || !items) return <Loading />;
  return (
    <Card pad={false} title="Stock movement history" actions={<BasisTag basis="fifo" />}>
      {items.length === 0 ? <Empty title="No movements yet" /> : (
        <div className="table-wrap"><table><thead><tr><th>When</th>{showIngredient && <th>Ingredient</th>}<th>Type</th><th className="num">Quantity</th><th className="num">Cost</th><th className="hide-sm">Batches used</th><th className="hide-sm">By</th></tr></thead>
          <tbody>{items.map((m) => (
            <tr key={m.id}><td className="small">{dateTime(m.occurred_at)}</td>
              {showIngredient && <td><Link to={`/ingredients/${m.ingredient_id}`}>{m.ingredient_name}</Link></td>}
              <td>{MOVE_LABEL[m.type] || m.type}{m.reason && <div className="muted small">{m.reason.replace(/_/g, " ")}</div>}{m.note && <div className="muted small">{m.note}</div>}
                {Number(m.shortfall_quantity) > 0 && <div><span className="badge warn">shortfall {smartQty(m.shortfall_quantity, m.base_unit)}</span></div>}</td>
              <td className="num" style={{ color: Number(m.quantity_base) < 0 ? "var(--bad)" : "var(--good)" }}>{Number(m.quantity_base) > 0 ? "+" : ""}{smartQty(m.quantity_base, m.base_unit)}</td>
              <td className="num">{money(m.total_cost)}</td>
              <td className="hide-sm small">{m.allocations.length ? m.allocations.map((a: any) => `${smartQty(a.quantity, m.base_unit)} @ ${unitCost(a.unit_cost, m.base_unit)}`).join("; ") : "—"}</td>
              <td className="hide-sm small">{m.created_by}</td></tr>))}</tbody></table></div>)}
    </Card>
  );
}

function PriceModal({ ing, onClose }: { ing: Ingredient; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ quantity: "1", unit: ing.purchase_unit, amount: "", effective_date: today(), note: "" });
  const m = useMutation({
    mutationFn: () => post(`/ingredients/${ing.id}/prices`, { ...f, note: f.note || null }),
    onSuccess: () => { qc.invalidateQueries(); toast("good", "Price recorded and recipes re-costed"); onClose(); },
  });
  return (
    <Modal title={`Record a quoted price for ${ing.name}`} onClose={onClose}>
      <p className="muted small" style={{ marginTop: 0 }}>Use this for supplier quotes or market prices. It updates the current cost used for recipe pricing. It does <strong>not</strong> add stock — confirm a purchase for that.</p>
      <ErrorBox error={m.error} />
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="form-grid">
          <Field label="Quantity">{(i) => <input id={i} type="number" step="any" min="0" required value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />}</Field>
          <Field label="Unit">{(i) => <select id={i} value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })}>{unitsFor(ing).map((u) => <option key={u}>{u}</option>)}</select>}</Field>
          <Field label="Total price">{(i) => <input id={i} type="number" step="any" min="0" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
          <Field label="Effective date">{(i) => <input id={i} type="date" required value={f.effective_date} onChange={(e) => setF({ ...f, effective_date: e.target.value })} />}</Field>
        </div>
        <div style={{ marginTop: 12 }}><Field label="Note">{(i) => <input id={i} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. Quote from Nakasero market" />}</Field></div>
        <div className="form-actions"><Button variant="secondary" type="button" onClick={onClose}>Cancel</Button><Button busy={m.isPending} type="submit">Save price</Button></div>
      </form>
    </Modal>
  );
}
