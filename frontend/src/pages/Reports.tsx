import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { BasisTag, Card, Empty, ErrorBox, Field, Loading, Page, Tabs } from "../components/ui";
import { get, qs } from "../lib/api";
import { date, dateTime, money, num, pct, qty, today, unitCost } from "../lib/format";

export function toCsv(rows: Record<string, any>[], cols: [string, string][]): string {
  // Neutralise spreadsheet formulas (CSV injection): a cell starting with = + - @ TAB CR is prefixed with '
  const esc = (v: any) => {
    let s = String(v ?? "");
    if (/^[=+\-@\t\r]/.test(s) && !/^-?\d+(\.\d+)?$/.test(s)) s = "'" + s;
    return `"${s.replace(/"/g, '""')}"`;
  };
  return [cols.map((c) => esc(c[1])).join(","), ...rows.map((r) => cols.map((c) => esc(r[c[0]])).join(","))].join("\n");
}
function download(name: string, csv: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

export default function Reports() {
  const [tab, setTab] = useState("valuation");
  const first = today().slice(0, 8) + "01";
  const [range, setRange] = useState({ date_from: first, date_to: today() });
  return (
    <Page title="Reports" subtitle="Each report states its cost basis: FIFO actual (what stock really cost) or current prices (for pricing decisions).">
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "valuation", label: "Inventory valuation" }, { id: "consumption", label: "Usage & waste" },
        { id: "purchases", label: "Purchases" }, { id: "recipes", label: "Recipe food cost" }, { id: "prices", label: "Price changes" }, { id: "integrity", label: "Data checks" }]} />
      {(tab === "consumption" || tab === "purchases") && (
        <div className="toolbar">
          <Field label="From">{(i) => <input id={i} type="date" value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} />}</Field>
          <Field label="To">{(i) => <input id={i} type="date" value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} />}</Field>
        </div>)}
      {tab === "valuation" && <Valuation />}
      {tab === "consumption" && <Consumption range={range} />}
      {tab === "purchases" && <Purchases range={range} />}
      {tab === "recipes" && <RecipeCosts />}
      {tab === "prices" && <Prices />}
      {tab === "integrity" && <Integrity />}
    </Page>
  );
}

function Valuation() {
  const q = useQuery({ queryKey: ["rep-valuation"], queryFn: () => get("/reports/inventory-valuation") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const rows = q.data.items.filter((r: any) => num(r.on_hand_base) > 0);
  return (
    <Card pad={false} title={<>Inventory valuation — {money(q.data.total_value)}</>} actions={<><BasisTag basis="fifo" />
      <button className="btn secondary sm" onClick={() => download(`inventory-valuation-${today()}.csv`, toCsv(rows, [["code", "Code"], ["name", "Ingredient"], ["on_hand", "On hand"], ["display_unit", "Unit"], ["inventory_value", "Value (FIFO)"]]))}>Export CSV</button></>}>
      <p className="muted small card-body" style={{ margin: 0 }}>As of {dateTime(q.data.as_of)}. Value = remaining quantity of each batch × that batch's purchase cost.</p>
      {rows.length === 0 ? <Empty title="No stock on hand" /> : (
        <div className="table-wrap"><table><thead><tr><th>Ingredient</th><th className="num">On hand</th><th className="num hide-sm">Batches</th><th className="num">Value</th><th className="num hide-sm">Share</th></tr></thead>
          <tbody>{rows.map((r: any) => (<tr key={r.ingredient_id}><td><Link to={`/ingredients/${r.ingredient_id}`}>{r.name}</Link></td><td className="num">{qty(r.on_hand, r.display_unit)}</td>
            <td className="num hide-sm">{r.open_batches}</td><td className="num">{money(r.inventory_value)}</td><td className="num hide-sm">{pct(num(r.inventory_value) / num(q.data.total_value), 0)}</td></tr>))}</tbody>
          <tfoot><tr><td>Total</td><td /><td className="hide-sm" /><td className="num">{money(q.data.total_value)}</td><td className="hide-sm" /></tr></tfoot></table></div>)}
    </Card>
  );
}

function Consumption({ range }: { range: any }) {
  const q = useQuery({ queryKey: ["rep-consumption", range], queryFn: () => get(`/reports/consumption${qs(range)}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  return (
    <div className="grid cols-2">
      <Card title="Ingredient cost consumed" actions={<BasisTag basis="fifo" />}>
        <dl className="kv"><dt>Production (recipes)</dt><dd className="num">{money(d.production)}</dd><dt>Manual usage</dt><dd className="num">{money(d.consumption)}</dd>
          <dt>Waste</dt><dd className="num">{money(d.waste)}</dd><dt>Stock-count shortages</dt><dd className="num">{money(d.count_shortage)}</dd>
          <dt><strong>Total</strong></dt><dd className="num"><strong>{money(d.total)}</strong></dd></dl>
        <p className="muted small">{date(d.from)} – {date(d.to)}</p>
      </Card>
      <Card title="Waste by reason" actions={<BasisTag basis="fifo" />} pad={false}>
        {Object.keys(d.waste_by_reason).length === 0 ? <Empty title="No waste recorded in this period" /> : (
          <ul className="list-plain">{Object.entries(d.waste_by_reason).map(([k, v]) => <li key={k}><span style={{ textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</span><strong>{money(v as string)}</strong></li>)}</ul>)}
      </Card>
    </div>
  );
}

function Purchases({ range }: { range: any }) {
  const q = useQuery({ queryKey: ["rep-purchases", range], queryFn: () => get(`/reports/purchases${qs(range)}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <Card pad={false} title={<>Purchases by supplier — {money(q.data.total)}</>}>
      {!q.data.by_supplier.length ? <Empty title="No purchases in this period" /> : (
        <div className="table-wrap"><table><thead><tr><th>Supplier</th><th className="num">Invoices</th><th className="num">Total</th></tr></thead>
          <tbody>{q.data.by_supplier.map((r: any) => <tr key={r.supplier}><td>{r.supplier}</td><td className="num">{r.invoices}</td><td className="num">{money(r.total)}</td></tr>)}</tbody></table></div>)}
    </Card>
  );
}

function RecipeCosts() {
  const q = useQuery({ queryKey: ["rep-recipes"], queryFn: () => get("/reports/recipe-costs") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <Card pad={false} title="Recipe food cost" actions={<BasisTag basis="current" />}>
      {!q.data.items.length ? <Empty title="No published recipes" /> : (
        <div className="table-wrap"><table><thead><tr><th>Recipe</th><th className="num">Cost / portion</th><th className="num">Menu price</th><th className="num">Food cost</th><th className="num hide-sm">Target</th><th className="num">Suggested price</th><th className="hide-sm">Last re-costed</th></tr></thead>
          <tbody>{q.data.items.map((r: any) => (<tr key={r.recipe_id}><td><Link to={`/recipes/${r.recipe_id}`}>{r.name}</Link> <span className="muted small">v{r.version_no}</span></td>
            <td className="num">{money(r.cost_per_portion)}</td><td className="num">{money(r.selling_price)}</td>
            <td className="num">{r.food_cost_rate ? <span className={`badge ${r.exceeds_target ? "bad" : "good"}`}>{pct(r.food_cost_rate)}</span> : "—"}</td>
            <td className="num hide-sm">{pct(r.target_food_cost_rate, 0)}</td><td className="num">{money(r.recommended_price)}</td><td className="hide-sm small">{dateTime(r.computed_at)}</td></tr>))}</tbody></table></div>)}
    </Card>
  );
}

function Prices() {
  const q = useQuery({ queryKey: ["rep-prices"], queryFn: () => get<any[]>("/reports/price-changes?limit=100") });
  if (q.isLoading) return <Loading />;
  return (
    <Card pad={false} title="Ingredient price changes" actions={<BasisTag basis="current" />}>
      {!q.data?.length ? <Empty title="No price changes yet" /> : (
        <div className="table-wrap"><table><thead><tr><th>Date</th><th>Ingredient</th><th className="hide-sm">Supplier</th><th className="num">Previous</th><th className="num">New</th><th className="num">Change</th></tr></thead>
          <tbody>{q.data.map((p, i) => (<tr key={i}><td>{date(p.effective_date)}</td><td><Link to={`/ingredients/${p.ingredient_id}`}>{p.ingredient}</Link></td><td className="hide-sm">{p.supplier || "Quote"}</td>
            <td className="num">{unitCost(p.previous_cost_per_base_unit, p.base_unit)}</td><td className="num">{unitCost(p.cost_per_base_unit, p.base_unit)}</td>
            <td className="num"><span className={`badge ${num(p.change_rate) > 0 ? "bad" : "good"}`}>{num(p.change_rate) > 0 ? "+" : ""}{pct(p.change_rate)}</span></td></tr>))}</tbody></table></div>)}
    </Card>
  );
}

function Integrity() {
  const q = useQuery({ queryKey: ["rep-integrity"], queryFn: () => get("/reports/integrity") });
  if (q.isLoading) return <Loading label="Checking records…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const label: Record<string, string> = {
    batch_remaining_matches_allocations: "Every batch's remaining quantity matches its recorded FIFO allocations",
    inventory_value_matches_movements: "Inventory value equals receipts minus FIFO-costed outflows",
    supplier_ledger_matches_invoices: "Supplier balances equal the sum of outstanding invoices",
    no_negative_batches: "No stock batch is negative",
  };
  return (
    <Card title="Data integrity checks">
      <div className={`alert ${q.data.ok ? "good" : "bad"}`}>{q.data.ok ? "All checks passed — dashboard figures reconcile with the underlying records." : "A check failed. Please contact support before posting more transactions."}</div>
      <ul className="list-plain" style={{ margin: "0 -16px" }}>{q.data.checks.map((c: any) => (<li key={c.name}><span>{label[c.name] || c.name}</span>{c.ok ? <span className="badge good">pass</span> : <span className="badge bad">fail</span>}</li>))}</ul>
      <p className="muted small">Checked {dateTime(q.data.checked_at)}</p>
    </Card>
  );
}
