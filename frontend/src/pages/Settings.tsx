import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, Loading, Modal, Page, Tabs, useToast } from "../components/ui";
import { get, patch, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useConnection } from "../lib/connection";
import { dateTime, pct } from "../lib/format";

const ROLES: Record<string, string> = {
  owner: "Everything, incl. users, settings and voiding payments", manager: "All operations except users, settings and voiding payments",
  purchaser: "Suppliers, purchases and payments", kitchen: "Recipes, usage, production, waste and counts", viewer: "Read-only",
};

export default function Settings() {
  const { me, can } = useAuth();
  const [tab, setTab] = useState(can("user.manage") ? "users" : "system");
  return (
    <Page title="Settings" subtitle={`${me?.branch.name} (${me?.branch.code}) · ${me?.branch.currency}`}>
      <Tabs value={tab} onChange={setTab} tabs={[...(can("user.manage") ? [{ id: "users", label: "Users & roles" }] : []),
        ...(can("settings.manage") ? [{ id: "branch", label: "Costing settings" }, { id: "audit", label: "Audit log" }] : []), { id: "system", label: "System status" }]} />
      {tab === "users" && <Users />}
      {tab === "branch" && <Branch />}
      {tab === "audit" && <Audit />}
      {tab === "system" && <System />}
    </Page>
  );
}

function Users() {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["users"], queryFn: () => get<any[]>("/users") });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ email: "", display_name: "", password: "", role: "kitchen" });
  const create = useMutation({ mutationFn: () => post("/users", f), onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("good", "User created"); setOpen(false); } });
  const upd = useMutation({ mutationFn: ({ id, body }: any) => patch(`/users/${id}`, body), onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("good", "User updated"); } });
  return (
    <Card pad={false} title="Users" actions={<Button onClick={() => setOpen(true)}>Add user</Button>}>
      <ErrorBox error={upd.error} />
      {q.isLoading ? <Loading /> : (
        <div className="table-wrap"><table><thead><tr><th>Name</th><th className="hide-sm">Email</th><th>Role</th><th>Status</th></tr></thead>
          <tbody>{q.data!.map((u) => (<tr key={u.id}><td>{u.display_name}</td><td className="hide-sm">{u.email}</td>
            <td><select value={u.role} disabled={u.id === me?.user.id} onChange={(e) => upd.mutate({ id: u.id, body: { role: e.target.value } })} aria-label={`Role for ${u.display_name}`}>
              {Object.keys(ROLES).map((r) => <option key={r} value={r}>{r}</option>)}</select></td>
            <td>{u.id !== me?.user.id ? <Button variant="ghost" className="sm" onClick={() => upd.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Deactivate" : "Activate"}</Button> : <span className="muted small">you</span>}
              {!u.is_active && <span className="badge neutral">inactive</span>}</td></tr>))}</tbody></table></div>)}
      <div className="card-body"><dl className="kv small">{Object.entries(ROLES).map(([r, d]) => <Fragment key={r}><dt style={{ textTransform: "capitalize" }}>{r}</dt><dd>{d}</dd></Fragment>)}</dl></div>
      {open && (
        <Modal title="Add user" onClose={() => setOpen(false)}>
          <ErrorBox error={create.error} />
          <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="form-grid">
              <Field label="Name">{(i) => <input id={i} required value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />}</Field>
              <Field label="Email">{(i) => <input id={i} type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />}</Field>
              <Field label="Temporary password" hint="At least 10 characters">{(i) => <input id={i} type="password" minLength={10} required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />}</Field>
              <Field label="Role" hint={ROLES[f.role]}>{(i) => <select id={i} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{Object.keys(ROLES).map((r) => <option key={r}>{r}</option>)}</select>}</Field>
            </div>
            <div className="form-actions"><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" busy={create.isPending}>Create user</Button></div>
          </form>
        </Modal>)}
    </Card>
  );
}

function Branch() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const [target, setTarget] = useState(String(Number(me!.branch.default_target_food_cost_rate) * 100));
  const [tax, setTax] = useState(me!.branch.include_tax_in_cost);
  const m = useMutation({ mutationFn: () => patch("/settings/branch", { default_target_food_cost_rate: String(Number(target) / 100), include_tax_in_cost: tax }),
    onSuccess: async () => { await reload(); toast("good", "Settings saved"); } });
  return (
    <Card title="Costing settings">
      <ErrorBox error={m.error} />
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <div className="form-grid">
          <Field label="Default target food cost %" hint={`Used for new recipes. Currently ${pct(me!.branch.default_target_food_cost_rate, 0)}`}>{(i) => <input id={i} type="number" min={1} max={99} step="any" value={target} onChange={(e) => setTarget(e.target.value)} />}</Field>
          <Field label="Purchase tax in stock cost" hint="Turn on only if this restaurant cannot reclaim VAT">{(i) => <select id={i} value={tax ? "1" : "0"} onChange={(e) => setTax(e.target.value === "1")}><option value="0">Exclude tax (VAT registered)</option><option value="1">Include tax in cost</option></select>}</Field>
        </div>
        <div className="form-actions"><Button type="submit" busy={m.isPending}>Save</Button></div>
      </form>
    </Card>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => get<any[]>("/audit-log?limit=200") });
  if (q.isLoading) return <Loading />;
  return (
    <Card pad={false} title="Audit log (read-only)">
      {!q.data?.length ? <Empty title="No entries" /> : (
        <div className="table-wrap"><table><thead><tr><th>When</th><th>User</th><th>Action</th><th className="hide-sm">Details</th></tr></thead>
          <tbody>{q.data.map((a) => (<tr key={a.id}><td className="small">{dateTime(a.at)}</td><td>{a.user || "system"}</td><td><span className="mono">{a.action}</span></td>
            <td className="hide-sm small mono" style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.detail ? JSON.stringify(a.detail) : ""}</td></tr>))}</tbody></table></div>)}
    </Card>
  );
}

function System() {
  const c = useConnection();
  const sync = useQuery({ queryKey: ["sync-status"], queryFn: () => get("/sync/status"), retry: false });
  return (
    <div className="grid cols-2">
      <Card title="Local branch server">
        <dl className="kv">
          <dt>Connection</dt><dd>{c.serverReachable ? <span className="badge good">connected</span> : <span className="badge bad">unreachable</span>}</dd>
          <dt>Database</dt><dd>{c.health?.database ?? "—"}</dd>
          <dt>Disk free</dt><dd>{c.health ? `${c.health.disk_free_percent}% (${c.health.disk})` : "—"}</dd>
          <dt>Mode</dt><dd>Local branch — works without internet</dd>
          <dt>This device</dt><dd>{c.deviceNetwork ? "network on" : "no network"}</dd>
        </dl>
      </Card>
      <Card title="Cloud synchronisation">
        {sync.data ? (<>
          <dl className="kv"><dt>Status</dt><dd><span className="badge warn">not enabled in this release</span></dd>
            <dt>Changes stored for sync</dt><dd>{sync.data.pending_events}</dd><dt>Oldest waiting</dt><dd>{dateTime(sync.data.oldest_pending_at)}</dd></dl>
          <p className="muted small">{sync.data.note}</p></>) : <ErrorBox error={sync.error} />}
      </Card>
    </div>
  );
}
