import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || (can("user.manage") ? "users" : "sync");
  const setTab = (t: string) => setParams({ tab: t });
  return (
    <Page title="Settings" subtitle={`${me?.branch.name} (${me?.branch.code}) · ${me?.branch.currency}`}>
      <Tabs value={tab} onChange={setTab} tabs={[...(can("user.manage") ? [{ id: "users", label: "Users & roles" }] : []),
        { id: "sync", label: "Cloud sync" },
        ...(can("settings.manage") ? [{ id: "devices", label: "Devices" }, { id: "branch", label: "Costing settings" }, { id: "audit", label: "Audit log" }] : []),
        { id: "system", label: "System status" }]} />
      {tab === "users" && <Users />}
      {tab === "sync" && <SyncPanel />}
      {tab === "devices" && <Devices />}
      {tab === "branch" && <Branch />}
      {tab === "audit" && <Audit />}
      {tab === "system" && <System />}
    </Page>
  );
}

const SYNC_STATE: Record<string, [string, string]> = {
  in_sync: ["good", "In sync"], syncing: ["info", "Sending changes"], offline: ["warn", "Cloud unreachable — retrying"],
  blocked: ["bad", "Blocked by a rejected change"], halted: ["bad", "Halted — needs an operator"], not_registered: ["neutral", "Not set up"],
};

function SyncPanel() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const st = useQuery({ queryKey: ["sync-status"], queryFn: () => get("/sync/status"), refetchInterval: 10000 });
  const unsent = useQuery({ queryKey: ["sync-queue", "unsent"], queryFn: () => get<any[]>("/sync/queue?state=unsent"), enabled: can("settings.manage") });
  const cmds = useQuery({ queryKey: ["sync-commands"], queryFn: () => get<any[]>("/sync/commands"), enabled: can("settings.manage") });
  const [reg, setReg] = useState({ cloud_url: "https://", registration_code: "" });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["sync-status"] }); qc.invalidateQueries({ queryKey: ["sync-queue"] }); qc.invalidateQueries({ queryKey: ["health"] }); qc.invalidateQueries({ queryKey: ["sync-commands"] }); };
  const register = useMutation({ mutationFn: () => post("/sync/register", reg), onSuccess: () => { refresh(); toast("good", "Registered with the cloud. The first full copy will be sent now."); } });
  const run = useMutation({ mutationFn: () => post("/sync/run"), onSuccess: (r: any) => {
    refresh(); qc.invalidateQueries();
    toast(r.result.state === "ok" ? "good" : "bad", r.result.state === "ok" ? `Sync complete — ${r.result.push?.acked ?? 0} change(s) acknowledged` : `Sync failed: ${r.result.error || r.result.reason || r.result.state}`);
  } });
  const retry = useMutation({ mutationFn: () => post("/sync/retry-failed"), onSuccess: () => { refresh(); toast("info", "Rejected changes queued for another attempt"); } });
  const snap = useMutation({ mutationFn: () => post("/sync/resnapshot"), onSuccess: () => { refresh(); toast("info", "A full copy will be sent on the next sync"); } });
  if (st.isLoading) return <Loading />;
  if (st.error) return <ErrorBox error={st.error} />;
  const s = st.data;
  const [tone, label] = SYNC_STATE[s.state] || ["neutral", s.state];
  const q = s.queue;
  return (
    <div className="stack">
      <ErrorBox error={register.error || run.error || retry.error || snap.error} />
      <div className="grid cols-4">
        <div className="stat card"><div className="stat-label">Cloud sync</div><div className="stat-value" style={{ fontSize: "1.1rem" }}><span className={`badge ${tone}`}>{label}</span></div>
          <div className="stat-hint">Last success: {dateTime(s.last_success_at)}</div></div>
        <div className="stat card"><div className="stat-label">Waiting to send</div><div className={`stat-value ${q.pending + q.retry ? "warn" : "good"}`}>{q.pending + q.retry}</div>
          <div className="stat-hint">{q.oldest_unsent_at ? `oldest from ${dateTime(q.oldest_unsent_at)}` : "nothing waiting"}</div></div>
        <div className="stat card"><div className="stat-label">Rejected (needs action)</div><div className={`stat-value ${q.failed ? "bad" : "good"}`}>{q.failed}</div>
          <div className="stat-hint">{q.acked} acknowledged by the cloud</div></div>
        <div className="stat card"><div className="stat-label">Receipts not yet in cloud</div><div className={`stat-value ${q.files_pending ? "warn" : "good"}`}>{q.files_pending}</div>
          <div className="stat-hint">Stored safely on this server</div></div>
      </div>
      {s.halted_reason && <div className="alert bad"><strong>Synchronisation halted.</strong> {s.halted_reason} Follow the recovery runbook (docs/SYNC-RUNBOOK.md); nothing has been deleted.</div>}
      {s.last_error && !s.halted_reason && <div className="alert warn">Last attempt: {s.last_error}. The restaurant keeps working; changes are kept and retried automatically with back-off.</div>}
      {!s.registered ? (
        can("settings.manage") && <Card title="Connect this restaurant to the cloud">
          <p className="muted small" style={{ marginTop: 0 }}>Optional. The restaurant works fully without it. Connecting adds an off-site copy, remote reports for the owner and remote management. Ask your administrator for the cloud address and a one-time registration code.</p>
          <form onSubmit={(e) => { e.preventDefault(); register.mutate(); }}>
            <div className="form-grid">
              <Field label="Cloud address">{(i) => <input id={i} type="url" required value={reg.cloud_url} onChange={(e) => setReg({ ...reg, cloud_url: e.target.value })} />}</Field>
              <Field label="Registration code">{(i) => <input id={i} required value={reg.registration_code} placeholder="ABCD-1234-…" onChange={(e) => setReg({ ...reg, registration_code: e.target.value })} />}</Field>
            </div>
            <div className="form-actions"><Button type="submit" busy={register.isPending}>Connect</Button></div>
          </form>
        </Card>
      ) : (
        <Card title="Connection" actions={can("settings.manage") && <>
          {q.failed > 0 && <Button variant="secondary" className="sm" busy={retry.isPending} onClick={() => retry.mutate()}>Retry rejected</Button>}
          <Button variant="secondary" className="sm" busy={snap.isPending} onClick={() => snap.mutate()}>Send full copy</Button>
          <Button className="sm" busy={run.isPending} onClick={() => run.mutate()}>Sync now</Button></>}>
          <dl className="kv"><dt>Cloud</dt><dd className="mono">{s.cloud_url}</dd><dt>Registered</dt><dd>{dateTime(s.registered_at)}</dd>
            <dt>Last attempt</dt><dd>{dateTime(s.last_attempt_at)}</dd><dt>Cloud has up to change #</dt><dd>{s.cloud_high_water ?? "—"}</dd></dl>
          <p className="muted small">Changes are sent in the order they happened and are only marked as synced when the cloud returns a signed acknowledgement. Nothing is deleted before that.</p>
        </Card>)}
      {can("settings.manage") && s.registered && (
        <div className="grid cols-2">
          <Card title="Not yet in the cloud" pad={false}>
            {!unsent.data?.length ? <Empty title="Nothing waiting" /> : (
              <div className="table-wrap"><table><thead><tr><th>#</th><th>Change</th><th>Status</th><th className="hide-sm">Detail</th></tr></thead>
                <tbody>{unsent.data.map((e) => (<tr key={e.id}><td className="mono">{e.sequence}</td><td>{e.topic}<div className="muted small">{dateTime(e.created_at)} · {e.rows} rows</div></td>
                  <td><span className={`badge ${e.status === "failed" ? "bad" : e.status === "retry" ? "warn" : "info"}`}>{e.status}</span>{e.attempts > 0 && <div className="muted small">{e.attempts} attempt(s)</div>}</td>
                  <td className="hide-sm small">{e.last_error}{e.next_attempt_at && <div className="muted">next try {dateTime(e.next_attempt_at)}</div>}</td></tr>))}</tbody></table></div>)}
          </Card>
          <Card title="Remote changes from the cloud" pad={false}>
            {!cmds.data?.length ? <Empty title="No remote changes received" /> : (
              <ul className="list-plain">{cmds.data.map((c) => (<li key={c.id}><span>{c.type}<div className="muted small">{dateTime(c.received_at)}{c.result?.by ? ` · by ${c.result.by}` : ""}{c.result?.error ? ` · ${c.result.error}` : ""}</div></span>
                <span className={`badge ${c.status === "applied" ? "good" : c.status === "conflict" ? "warn" : "bad"}`}>{c.status}</span></li>))}</ul>)}
          </Card>
        </div>)}
    </div>
  );
}

function Devices() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["devices"], queryFn: () => get<any[]>("/devices") });
  const revoke = useMutation({ mutationFn: (id: string) => post(`/devices/${id}/revoke`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["devices"] }); toast("good", "Device revoked — it can no longer make changes"); } });
  return (
    <Card pad={false} title="Registered devices">
      <p className="muted small card-body" style={{ margin: 0 }}>Every change records the device it came from. Revoke a lost or stolen phone or tablet to block it immediately.</p>
      <ErrorBox error={revoke.error} />
      {q.isLoading ? <Loading /> : (
        <div className="table-wrap"><table><thead><tr><th>Device</th><th className="hide-sm">Registered by</th><th>Since</th><th>Status</th><th /></tr></thead>
          <tbody>{q.data!.map((d) => (<tr key={d.id}><td>{d.name}{d.is_current && <span className="badge info" style={{ marginLeft: 6 }}>this device</span>}<div className="muted small mono">{d.id.slice(0, 8)}</div></td>
            <td className="hide-sm">{d.registered_by || "—"}</td><td className="small">{dateTime(d.created_at)}</td>
            <td>{d.revoked_at ? <span className="badge bad">revoked</span> : <span className="badge good">active</span>}</td>
            <td>{!d.revoked_at && !d.is_current && <Button variant="ghost" className="sm" onClick={() => revoke.mutate(d.id)}>Revoke</Button>}</td></tr>))}</tbody></table></div>)}
    </Card>
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
          <dl className="kv"><dt>Status</dt><dd>{(SYNC_STATE[sync.data.state] || [, sync.data.state])[1]}</dd>
            <dt>Changes waiting</dt><dd>{sync.data.pending_events}</dd><dt>Oldest waiting</dt><dd>{dateTime(sync.data.queue.oldest_unsent_at)}</dd>
            <dt>Last cloud success</dt><dd>{dateTime(sync.data.last_success_at)}</dd></dl>
          <p className="muted small">The restaurant never depends on the cloud for daily work.</p></>) : <ErrorBox error={sync.error} />}
      </Card>
    </div>
  );
}
