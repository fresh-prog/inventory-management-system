import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  BasisTag, Button, Card, Confirm, Empty, ErrorBox, Field, LinkButton, Loading, Page, SearchSelect, StatusBadge, Tabs, useToast,
} from "../components/ui";
import { get, post, put } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useDebounced } from "../lib/hooks";
import { dateTime, money, pct, qty, smartQty, unitCost } from "../lib/format";
import type { Ingredient, RecipeCost } from "../lib/types";
import { unitsFor, useIngredients } from "./Ingredients";

export function RecipesList() {
  const q = useQuery({ queryKey: ["recipes"], queryFn: () => get<any[]>("/recipes") });
  const nav = useNavigate();
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const rows = q.data!;
  return (
    <Card pad={false} title="Recipes" actions={<BasisTag basis="current" />}>
      {rows.length === 0 ? <Empty title="No recipes yet">Create a recipe to see its cost per portion and a suggested selling price.</Empty> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Recipe</th><th>Version</th><th className="num">Cost / portion</th><th className="num hide-sm">Selling price</th><th className="num">Food cost</th><th className="num hide-sm">Suggested price</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => nav(`/recipes/${r.id}`)}>
              <td><Link to={`/recipes/${r.id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link><div className="muted small">{r.category || r.code}</div></td>
              <td>{r.published_version_no ? <StatusBadge status="published" /> : <StatusBadge status="draft" />} {r.published_version_no && <span className="muted small">v{r.published_version_no}</span>}
                {r.has_draft && r.published_version_no && <span className="muted small"> + draft</span>}</td>
              <td className="num">{money(r.cost_per_portion)}</td><td className="num hide-sm">{money(r.selling_price)}</td>
              <td className="num">{r.food_cost_rate ? <span className={r.exceeds_target ? "badge bad" : "badge good"}>{pct(r.food_cost_rate)}</span> : "—"}
                {r.target_food_cost_rate && <div className="muted small">target {pct(r.target_food_cost_rate, 0)}</div>}</td>
              <td className="num hide-sm">{money(r.recommended_price)}</td></tr>))}</tbody>
        </table></div>)}
    </Card>
  );
}

type Line = { ingredient_id: string; quantity: string; unit: string; basis: "gross" | "net"; prep_loss_rate: string };
type Form = { code: string; name: string; category: string; portions_per_batch: string; portion_size_qty: string; portion_size_unit: string;
  cooking_shrinkage_rate: string; target_food_cost_rate: string; selling_price: string; prep_instructions: string;
  cooking_instructions: string; change_note: string; lines: Line[] };

const pctIn = (v: string | null | undefined) => (v === null || v === undefined || v === "" ? "" : String(+(Number(v) * 100).toFixed(4)));
const pctOut = (v: string) => (v === "" ? "0" : String(Number(v) / 100));

export function RecipeEditor() {
  const { id } = useParams();
  const editing = !!id;
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const ings = useIngredients();
  const existing = useQuery({ queryKey: ["recipe", id], queryFn: () => get(`/recipes/${id}`), enabled: editing });
  const [state, setState] = useState<Form | null>(null);
  const src = existing.data ? (existing.data.draft_version || existing.data.published_version) : null;
  const defaultTarget = pctIn(me?.branch.default_target_food_cost_rate);
  const form: Form = state ?? (src ? {
    code: existing.data.code, name: existing.data.name, category: existing.data.category || "",
    portions_per_batch: String(Number(src.portions_per_batch)), portion_size_qty: src.portion_size_qty ? String(Number(src.portion_size_qty)) : "",
    portion_size_unit: src.portion_size_unit || "g", cooking_shrinkage_rate: pctIn(src.cooking_shrinkage_rate),
    target_food_cost_rate: pctIn(src.target_food_cost_rate), selling_price: src.selling_price ? String(Number(src.selling_price)) : "",
    prep_instructions: src.prep_instructions || "", cooking_instructions: src.cooking_instructions || "", change_note: "",
    lines: src.lines.map((l: any) => ({ ingredient_id: l.ingredient_id, quantity: String(Number(l.quantity)), unit: l.unit, basis: l.basis, prep_loss_rate: pctIn(l.prep_loss_rate) })),
  } : {
    code: "", name: "", category: "", portions_per_batch: "1", portion_size_qty: "", portion_size_unit: "g", cooking_shrinkage_rate: "0",
    target_food_cost_rate: defaultTarget, selling_price: "", prep_instructions: "", cooking_instructions: "", change_note: "",
    lines: [{ ingredient_id: "", quantity: "", unit: "g", basis: "gross", prep_loss_rate: "0" }],
  });
  const set = (patch: Partial<Form>) => setState({ ...form, ...patch });
  const setLine = (i: number, p: Partial<Line>) => set({ lines: form.lines.map((l, j) => (j === i ? { ...l, ...p } : l)) });
  const byId = Object.fromEntries((ings.data || []).map((i) => [i.id, i])) as Record<string, Ingredient>;

  const versionBody = () => ({
    portions_per_batch: form.portions_per_batch || "1",
    portion_size_qty: form.portion_size_qty || null, portion_size_unit: form.portion_size_qty ? form.portion_size_unit : null,
    cooking_shrinkage_rate: pctOut(form.cooking_shrinkage_rate), target_food_cost_rate: form.target_food_cost_rate ? pctOut(form.target_food_cost_rate) : null,
    selling_price: form.selling_price || null, prep_instructions: form.prep_instructions || null,
    cooking_instructions: form.cooking_instructions || null, change_note: form.change_note || null,
    lines: form.lines.filter((l) => l.ingredient_id && Number(l.quantity) > 0).map((l) => ({ ...l, prep_loss_rate: pctOut(l.prep_loss_rate) })),
  });
  const previewBody = useDebounced(JSON.stringify(versionBody()), 350);
  const preview = useQuery<RecipeCost>({
    queryKey: ["cost-preview", previewBody],
    queryFn: () => { const b = JSON.parse(previewBody); delete b.change_note; delete b.prep_instructions; delete b.cooking_instructions; delete b.portion_size_qty; delete b.portion_size_unit; return post("/recipes/cost-preview", b); },
    enabled: JSON.parse(previewBody).lines.length > 0, placeholderData: (p) => p, retry: false,
  });
  const save = useMutation({
    mutationFn: () => editing ? put(`/recipes/${id}/draft`, { ...versionBody(), name: form.name, category: form.category || null })
      : post("/recipes", { code: form.code, name: form.name, category: form.category || null, version: versionBody() }),
    onSuccess: (r: any) => { qc.invalidateQueries({ queryKey: ["recipes"] }); qc.invalidateQueries({ queryKey: ["recipe", r.id] }); toast("good", "Recipe draft saved"); nav(`/recipes/${r.id}`); },
  });
  if (editing && existing.isLoading) return <Loading />;
  const c = preview.data;
  return (
    <Page title={editing ? `Edit ${form.name}` : "New recipe"} subtitle="Changes are saved as a draft version. Published versions are never changed.">
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <ErrorBox error={save.error} />
        <div className="grid cols-3">
          <div className="stack span-2">
            <Card title="Recipe">
              <div className="form-grid">
                <Field label="Name">{(i) => <input id={i} required value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Beef stew" />}</Field>
                <Field label="Code">{(i) => <input id={i} required disabled={editing} pattern="[A-Za-z0-9_\-]+" value={form.code} onChange={(e) => set({ code: e.target.value.toUpperCase() })} placeholder="STEW" />}</Field>
                <Field label="Category">{(i) => <input id={i} value={form.category} onChange={(e) => set({ category: e.target.value })} placeholder="Mains" />}</Field>
              </div>
            </Card>
            <Card title="Ingredients" pad={false}>
              <div className="table-wrap"><table className="line-table">
                <thead><tr><th style={{ minWidth: 180 }}>Ingredient</th><th style={{ width: 110 }}>Quantity</th><th style={{ width: 86 }}>Unit</th><th style={{ width: 130 }}>Entered as</th><th style={{ width: 96 }}>Prep loss %</th><th className="num">Cost</th><th /></tr></thead>
                <tbody>{form.lines.map((l, i) => {
                  const ing = byId[l.ingredient_id];
                  const cl = c?.lines.find((x) => x.ingredient_id === l.ingredient_id);
                  return (<tr key={i}>
                    <td><SearchSelect items={ings.data || []} value={l.ingredient_id} label={(x) => x.name} placeholder="Choose ingredient…"
                      onChange={(v) => { const g = byId[v]; setLine(i, { ingredient_id: v, unit: g?.consumption_unit || "g" }); }} /></td>
                    <td><input type="number" step="any" min="0" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} aria-label="Quantity" placeholder="Qty" /></td>
                    <td><select value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} aria-label="Unit">{unitsFor(ing).map((u) => <option key={u}>{u}</option>)}</select></td>
                    <td><select value={l.basis} onChange={(e) => setLine(i, { basis: e.target.value as any })} aria-label="Quantity basis"
                      title="Gross: amount taken from stock. Net: usable amount after trimming (the system adds the trim loss once).">
                      <option value="gross">As issued (gross)</option><option value="net">Usable (net)</option></select></td>
                    <td><input type="number" step="any" min="0" max="99" value={l.prep_loss_rate} onChange={(e) => setLine(i, { prep_loss_rate: e.target.value })} aria-label="Preparation loss percent" placeholder="Loss %" /></td>
                    <td className="num">{cl ? (cl.has_price ? money(cl.cost) : <span className="badge warn">no price</span>) : "—"}</td>
                    <td><button type="button" className="icon-btn" aria-label="Remove line" onClick={() => set({ lines: form.lines.filter((_, j) => j !== i) })}>×</button></td>
                  </tr>);
                })}</tbody></table></div>
              <div className="card-body"><Button type="button" variant="secondary" className="sm" onClick={() => set({ lines: [...form.lines, { ingredient_id: "", quantity: "", unit: "g", basis: "gross", prep_loss_rate: "0" }] })}>+ Add ingredient</Button>
                <p className="muted small">Gross = taken from stock (cost uses this amount). Net = usable amount after trimming; the system adds the prep loss once to work out what is taken from stock. Losses are never deducted twice.</p></div>
            </Card>
            <Card title="Yield & pricing">
              <div className="form-grid">
                <Field label="Portions per batch">{(i) => <input id={i} type="number" step="any" min="0" required value={form.portions_per_batch} onChange={(e) => set({ portions_per_batch: e.target.value })} />}</Field>
                <Field label="Portion size (optional)">{(i) => <div className="row" style={{ flexWrap: "nowrap" }}><input id={i} type="number" step="any" min="0" value={form.portion_size_qty} onChange={(e) => set({ portion_size_qty: e.target.value })} />
                  <select value={form.portion_size_unit} onChange={(e) => set({ portion_size_unit: e.target.value })} style={{ width: 80 }} aria-label="Portion unit">{["g", "kg", "ml", "l", "unit"].map((u) => <option key={u}>{u}</option>)}</select></div>}</Field>
                <Field label="Cooking shrinkage %" hint="Weight lost in cooking. Affects yield, not cost.">{(i) => <input id={i} type="number" step="any" min="0" max="99" value={form.cooking_shrinkage_rate} onChange={(e) => set({ cooking_shrinkage_rate: e.target.value })} />}</Field>
                <Field label="Target food cost %">{(i) => <input id={i} type="number" step="any" min="1" max="99" value={form.target_food_cost_rate} onChange={(e) => set({ target_food_cost_rate: e.target.value })} />}</Field>
                <Field label="Menu price (excl. tax)">{(i) => <input id={i} type="number" step="any" min="0" value={form.selling_price} onChange={(e) => set({ selling_price: e.target.value })} />}</Field>
              </div>
            </Card>
            <Card title="Method">
              <div className="grid cols-2">
                <Field label="Preparation instructions">{(i) => <textarea id={i} value={form.prep_instructions} onChange={(e) => set({ prep_instructions: e.target.value })} />}</Field>
                <Field label="Cooking instructions">{(i) => <textarea id={i} value={form.cooking_instructions} onChange={(e) => set({ cooking_instructions: e.target.value })} />}</Field>
              </div>
              {editing && <div style={{ marginTop: 12 }}><Field label="What changed?">{(i) => <input id={i} value={form.change_note} onChange={(e) => set({ change_note: e.target.value })} placeholder="e.g. Reduced beef to 4 kg" />}</Field></div>}
            </Card>
          </div>
          <div className="stack">
            <CostPanel cost={c} loading={preview.isFetching} error={preview.error} />
            <div className="form-actions" style={{ marginTop: 0 }}>
              <Button type="button" variant="secondary" onClick={() => nav(-1)}>Cancel</Button>
              <Button type="submit" busy={save.isPending}>Save draft</Button>
            </div>
          </div>
        </div>
      </form>
    </Page>
  );
}

export function CostPanel({ cost: c, loading, error }: { cost?: RecipeCost | null; loading?: boolean; error?: unknown }) {
  return (
    <Card title={<>Cost {loading && <span className="spinner" aria-label="Updating" />}</>} actions={<BasisTag basis="current" />}>
      <ErrorBox error={error} />
      {!c ? <p className="muted">Add ingredients to see costs.</p> : (
        <>
          {c.missing_prices.length > 0 && <div className="alert warn">No price yet for: {c.missing_prices.join(", ")}. Costs are incomplete.</div>}
          <dl className="kv">
            <dt>Batch cost</dt><dd className="num">{money(c.batch_cost)}</dd>
            <dt>Cost per portion</dt><dd className="num"><strong>{money(c.cost_per_portion)}</strong></dd>
            <dt>Target food cost</dt><dd className="num">{pct(c.target_food_cost_rate, 0)}</dd>
            <dt>Suggested price</dt><dd className="num"><strong>{money(c.recommended_price)}</strong></dd>
            {c.selling_price && <>
              <dt>Menu price</dt><dd className="num">{money(c.selling_price)}</dd>
              <dt>Food cost</dt><dd className="num"><span className={`badge ${c.exceeds_target ? "bad" : "good"}`}>{pct(c.food_cost_rate)}</span></dd>
              <dt>Gross contribution</dt><dd className="num">{money(c.gross_contribution)}</dd></>}
          </dl>
          {c.selling_price && <div className={`bar ${c.exceeds_target ? "over" : ""}`} style={{ marginTop: 12 }} aria-hidden><span style={{ width: `${Math.min(100, Number(c.food_cost_rate) * 100)}%` }} /></div>}
          <p className="muted small">Suggested price = cost per portion ÷ target food cost. Based on the latest purchase prices.</p>
          {Number(c.yield.raw_mass_g) > 0 && <>
            <h3 style={{ marginTop: 14 }}>Yield (weighed ingredients)</h3>
            <dl className="kv small">
              <dt>Taken from stock</dt><dd className="num">{smartQty(c.yield.raw_mass_g, "g")}</dd>
              <dt>Usable after prep</dt><dd className="num">{smartQty(c.yield.usable_mass_g, "g")}</dd>
              <dt>After cooking ({pct(c.yield.cooking_shrinkage_rate, 0)} shrink)</dt><dd className="num">{smartQty(c.yield.cooked_mass_g, "g")}</dd>
              <dt>Per portion</dt><dd className="num">{qty(c.yield.cooked_mass_per_portion_g, "g", 0)}</dd>
            </dl></>}
        </>)}
    </Card>
  );
}

export function RecipeDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("cost");
  const [confirm, setConfirm] = useState(false);
  const q = useQuery({ queryKey: ["recipe", id], queryFn: () => get(`/recipes/${id}`) });
  const hist = useQuery({ queryKey: ["recipe-history", id], queryFn: () => get<any[]>(`/recipes/${id}/cost-history`), enabled: tab === "history" });
  const runs = useQuery({ queryKey: ["recipe-runs", id], queryFn: () => get<any[]>(`/recipes/${id}/production-runs`), enabled: tab === "runs" });
  const publish = useMutation({
    mutationFn: (v: number) => post(`/recipes/${id}/versions/${v}/publish`),
    onSuccess: () => { qc.invalidateQueries(); toast("good", "Recipe approved and published"); setConfirm(false); },
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data;
  const v = r.published_version, d = r.draft_version;
  const shown = tab === "draft" ? d : v || d;
  const shownCost: RecipeCost | null = tab === "draft" ? r.draft_cost : r.cost || r.draft_cost;
  return (
    <Page title={r.name} subtitle={<>{r.category || r.code} · {v ? <>Published version {v.version_no}</> : "Not yet published"}{d && <> · draft v{d.version_no} in progress</>}</>}
      actions={<>
        {can("recipe.write") && <LinkButton to={`/recipes/${r.id}/edit`} variant="secondary">{d ? "Edit draft" : "Create new version"}</LinkButton>}
        {can("recipe.publish") && d && <Button onClick={() => setConfirm(true)}>Approve & publish v{d.version_no}</Button>}
        {can("production.record") && v && <LinkButton to={`/stock?tab=produce&recipe=${r.id}`}>Record production</LinkButton>}
      </>}>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "cost", label: v ? `Published v${v.version_no}` : "Draft" }, ...(v && d ? [{ id: "draft", label: `Draft v${d.version_no}` }] : []),
        { id: "history", label: "Cost history" }, { id: "versions", label: "Versions" }, { id: "runs", label: "Production runs" }]} />
      {(tab === "cost" || tab === "draft") && shown && (
        <div className="grid cols-3">
          <div className="stack span-2">
            <Card title="Ingredient costs" pad={false} actions={<BasisTag basis="current" />}>
              <div className="table-wrap"><table><thead><tr><th>Ingredient</th><th className="num">Quantity</th><th className="num hide-sm">From stock</th><th className="num hide-sm">Unit cost</th><th className="num">Cost</th><th className="num hide-sm">Share</th></tr></thead>
                <tbody>{shownCost?.lines.map((l) => (
                  <tr key={l.ingredient_id}><td><Link to={`/ingredients/${l.ingredient_id}`}>{l.ingredient_name}</Link>
                    {l.basis === "net" && <div className="muted small">net, {pct(l.prep_loss_rate, 0)} prep loss</div>}
                    {l.basis === "gross" && Number(l.prep_loss_rate) > 0 && <div className="muted small">{pct(l.prep_loss_rate, 0)} trimmed (yield only)</div>}</td>
                    <td className="num">{qty(l.quantity, l.unit)}</td><td className="num hide-sm">{smartQty(l.gross_base_quantity, l.base_unit)}</td>
                    <td className="num hide-sm">{l.cost_per_base_unit ? unitCost(l.cost_per_base_unit, l.base_unit) : "—"}</td>
                    <td className="num">{l.has_price ? money(l.cost) : <span className="badge warn">no price</span>}</td><td className="num hide-sm">{pct(l.share_of_cost, 0)}</td></tr>))}</tbody>
                <tfoot><tr><td colSpan={2}>Batch total ({qty(shown.portions_per_batch)} portions)</td><td className="hide-sm" colSpan={2} /><td className="num">{money(shownCost?.batch_cost)}</td><td className="hide-sm" /></tr></tfoot></table></div>
            </Card>
            {(shown.prep_instructions || shown.cooking_instructions) && (
              <Card title="Method"><div className="grid cols-2">
                <div><h3>Preparation</h3><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{shown.prep_instructions || "—"}</p></div>
                <div><h3>Cooking</h3><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{shown.cooking_instructions || "—"}</p></div></div></Card>)}
          </div>
          <CostPanel cost={shownCost} />
        </div>)}
      {tab === "history" && (
        <Card pad={false} title="Cost snapshots" actions={<BasisTag basis="current" />}>
          <p className="muted small card-body" style={{ margin: 0 }}>A snapshot is saved when a version is published and every time an ingredient price changes. Snapshots are never edited.</p>
          {hist.isLoading ? <Loading /> : !hist.data?.length ? <Empty title="No snapshots yet">Publish the recipe to start tracking cost changes.</Empty> : (
            <div className="table-wrap"><table><thead><tr><th>When</th><th>Version</th><th>Why</th><th className="num">Cost / portion</th><th className="num">Change</th><th className="num hide-sm">Food cost</th><th className="num hide-sm">Suggested price</th></tr></thead>
              <tbody>{hist.data.map((h) => (
                <tr key={h.id}><td className="small">{dateTime(h.computed_at)}</td><td>v{h.version_no}</td><td className="small">{h.trigger === "publish" ? "Published" : h.trigger_ref}</td>
                  <td className="num">{money(h.cost_per_portion)}</td>
                  <td className="num">{h.change_per_portion && Number(h.change_per_portion) !== 0 ? <span className={`badge ${Number(h.change_per_portion) > 0 ? "bad" : "good"}`}>{money(h.change_per_portion, { sign: true })}</span> : "—"}</td>
                  <td className="num hide-sm">{h.food_cost_rate ? pct(h.food_cost_rate) : "—"}{h.exceeds_target && " ⚠"}</td><td className="num hide-sm">{money(h.recommended_price)}</td></tr>))}</tbody></table></div>)}
        </Card>)}
      {tab === "versions" && (
        <Card pad={false} title="Versions"><ul className="list-plain">{r.versions.slice().reverse().map((x: any) => (
          <li key={x.version_no}><span><strong>v{x.version_no}</strong> <StatusBadge status={x.status} /> <span className="muted small">{x.change_note}</span></span>
            <span className="muted small">{x.published_at ? `published ${dateTime(x.published_at)}` : `created ${dateTime(x.created_at)}`}</span></li>))}</ul></Card>)}
      {tab === "runs" && (
        <Card pad={false} title="Production runs" actions={<BasisTag basis="fifo" />}>
          {runs.isLoading ? <Loading /> : !runs.data?.length ? <Empty title="No production recorded" /> : (
            <div className="table-wrap"><table><thead><tr><th>When</th><th>Version</th><th className="num">Portions</th><th className="num">Actual ingredient cost</th><th className="num">Per portion</th></tr></thead>
              <tbody>{runs.data.map((x) => (<tr key={x.id}><td>{dateTime(x.produced_at)}</td><td>v{x.version_no}</td><td className="num">{qty(x.actual_portions ?? x.expected_portions)}</td>
                <td className="num">{money(x.total_cost)}</td><td className="num">{money(x.cost_per_portion)}</td></tr>))}</tbody></table></div>)}
        </Card>)}
      {confirm && d && (
        <Confirm title={`Publish version ${d.version_no}?`} confirmLabel="Approve & publish" busy={publish.isPending} onClose={() => setConfirm(false)} onConfirm={() => publish.mutate(d.version_no)}>
          <ErrorBox error={publish.error} />
          <p>Version {d.version_no} becomes the approved recipe used for production. {v && <>Version {v.version_no} will be retired but kept in history.</>} Published versions cannot be edited.</p>
        </Confirm>)}
    </Page>
  );
}
