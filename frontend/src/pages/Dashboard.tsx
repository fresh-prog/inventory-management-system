import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { BasisTag, Card, Empty, ErrorBox, LinkButton, Loading, Page, Stat, StatusBadge } from "../components/ui";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import { date, dateTime, money, pct, qty, unitCost } from "../lib/format";

export default function Dashboard() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => get("/dashboard"), refetchInterval: 60_000 });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const c = d.consumption;
  return (
    <Page title="Dashboard" subtitle={<>Figures as of {dateTime(d.as_of)} · {date(d.period.from)} – {date(d.period.to)}</>}
      actions={<>
        {can("purchase.create") && <LinkButton to="/purchases/new">Record purchase</LinkButton>}
        {can("stock.consume") && <LinkButton to="/stock?tab=use" variant="secondary">Record usage</LinkButton>}
      </>}>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Inventory value" value={money(d.inventory_value.value)} hint={<BasisTag basis="fifo" />} to="/stock" />
        <Stat label="Purchases this month" value={money(d.month_purchases)} hint="Confirmed invoices" to="/purchases" />
        <Stat label="Owed to suppliers" value={money(d.outstanding_payables)} tone={Number(d.outstanding_payables) > 0 ? "warn" : undefined}
          hint="Unpaid + partially paid invoices" to="/suppliers" />
        <Stat label="Low-stock items" value={d.low_stock_count ?? d.low_stock.length} tone={(d.low_stock_count ?? d.low_stock.length) ? "bad" : "good"} hint="Below minimum level" to="/stock?filter=low" />
      </div>
      <div className="grid cols-2">
        <Card title="Low stock" actions={<Link to="/stock?filter=low">View stock</Link>} pad={false}>
          {d.low_stock.length === 0 ? <Empty title="All stock is above minimum levels" /> : (
            <ul className="list-plain">{d.low_stock.map((s: any) => (
              <li key={s.ingredient_id}><Link to={`/ingredients/${s.ingredient_id}`}>{s.name}</Link>
                <span className="num"><strong className={s.is_out ? "" : ""}>{qty(s.on_hand, s.display_unit)}</strong>
                  <span className="muted small"> / min {qty(s.min_stock, s.display_unit)}</span></span></li>))}</ul>)}
        </Card>
        <Card title="Ingredient usage this month" actions={<BasisTag basis="fifo" />}>
          <dl className="kv">
            <dt>Production (recipes)</dt><dd className="num">{money(c.production)}</dd>
            <dt>Manual usage</dt><dd className="num">{money(c.consumption)}</dd>
            <dt>Waste & spoilage</dt><dd className="num">{money(c.waste)}</dd>
            <dt>Stock-count shortages</dt><dd className="num">{money(c.count_shortage)}</dd>
            <dt><strong>Total consumed</strong></dt><dd className="num"><strong>{money(c.total)}</strong></dd>
          </dl>
        </Card>
        <Card title="Recent purchases" actions={<Link to="/purchases">All purchases</Link>} pad={false}>
          {d.recent_purchases.length === 0 ? <Empty title="No purchases yet" /> : (
            <div className="table-wrap"><table><thead><tr><th>Invoice</th><th className="hide-sm">Supplier</th><th className="num">Total</th><th>Status</th></tr></thead>
              <tbody>{d.recent_purchases.map((p: any) => (
                <tr key={p.id}><td><Link to={`/purchases/${p.id}`}>{p.invoice_ref}</Link><div className="muted small">{date(p.invoice_date)}</div></td>
                  <td className="hide-sm">{p.supplier}</td><td className="num">{money(p.total)}</td><td><StatusBadge status={p.payment_status} /></td></tr>))}
              </tbody></table></div>)}
        </Card>
        <Card title="Ingredient price changes" actions={<BasisTag basis="current" />} pad={false}>
          {d.price_changes.length === 0 ? <Empty title="No price changes recorded yet" /> : (
            <ul className="list-plain">{d.price_changes.map((p: any, i: number) => (
              <li key={i}><span><Link to={`/ingredients/${p.ingredient_id}`}>{p.ingredient}</Link>
                <div className="muted small">{unitCost(p.previous_cost_per_base_unit, p.base_unit)} → {unitCost(p.cost_per_base_unit, p.base_unit)}</div></span>
                <span className={`badge ${Number(p.change_rate) > 0 ? "bad" : "good"}`}>{Number(p.change_rate) > 0 ? "▲" : "▼"} {pct(Math.abs(Number(p.change_rate)))}</span></li>))}</ul>)}
        </Card>
        <Card title="Recipe food cost" className="span-2" actions={<><BasisTag basis="current" /><Link to="/ingredients?tab=recipes">All recipes</Link></>} pad={false}>
          {d.recipes.length === 0 ? <Empty title="No published recipes yet">Publish a recipe to track its food cost.</Empty> : (
            <div className="table-wrap"><table><thead><tr><th>Recipe</th><th className="num">Cost / portion</th><th className="num hide-sm">Price</th>
              <th className="num">Food cost</th><th className="num hide-sm">Target</th><th className="num">Suggested price</th></tr></thead>
              <tbody>{d.recipes.map((r: any) => (
                <tr key={r.recipe_id}><td><Link to={`/recipes/${r.recipe_id}`}>{r.name}</Link> {r.exceeds_target && <span className="badge bad">over target</span>}</td>
                  <td className="num">{money(r.cost_per_portion)}</td><td className="num hide-sm">{money(r.selling_price)}</td>
                  <td className="num">{r.food_cost_rate ? pct(r.food_cost_rate) : "—"}</td><td className="num hide-sm">{pct(r.target_food_cost_rate, 0)}</td>
                  <td className="num">{money(r.recommended_price)}</td></tr>))}</tbody></table></div>)}
        </Card>
      </div>
    </Page>
  );
}
