import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../lib/api";

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => { document.title = `${title} · KitchenLedger`; }, [title]);
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

export function Card({ title, actions, children, className = "", pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && <div className="card-head"><h2>{title}</h2>{actions && <div className="actions">{actions}</div>}</div>}
      <div className={pad ? "card-body" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone, to }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "warn" | "bad" | "good"; to?: string }) {
  const body = (
    <>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone || ""}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </>
  );
  return to ? <Link to={to} className="stat card link-card">{body}</Link> : <div className="stat card">{body}</div>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "good" | "warn" | "bad" | "info"; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function BasisTag({ basis }: { basis: "fifo" | "current" }) {
  return (
    <span className={`basis ${basis}`} title={basis === "fifo"
      ? "Actual cost of the stock batches used (first in, first out)"
      : "Projected cost at the latest purchase prices — used for pricing decisions"}>
      {basis === "fifo" ? "Cost basis: FIFO actual" : "Cost basis: current prices"}
    </span>
  );
}

/** Where a saved record is: never claims "cloud" before the cloud's signed acknowledgement. */
export function SyncBadge({ status }: { status?: string }) {
  if (!status) return null;
  const map: Record<string, [string, string, string]> = {
    local: ["neutral", "Saved on server", "Saved on the restaurant server (cloud sync not set up)."],
    pending: ["info", "Saved · cloud pending", "Saved on the restaurant server. Not yet confirmed by the cloud — it will be sent automatically."],
    cloud: ["good", "In cloud ✓", "Saved on the restaurant server and confirmed by the cloud."],
    attention: ["warn", "Cloud sync issue", "Saved on the restaurant server, but the cloud rejected it. See Settings → Cloud sync."],
  };
  const [tone, label, title] = map[status] || ["neutral", status, ""];
  return <span className={`badge ${tone}`} title={title}>{label}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone = ({ paid: "good", partially_paid: "warn", unpaid: "bad", voided: "neutral", confirmed: "good",
                  published: "good", draft: "info", retired: "neutral", open: "info", approved: "good" } as any)[status] || "neutral";
  return <Badge tone={tone}>{status.replace(/_/g, " ")}</Badge>;
}

export function Button({ variant = "primary", busy, children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; busy?: boolean }) {
  return (
    <button {...rest} className={`btn ${variant} ${rest.className || ""}`} disabled={rest.disabled || busy} aria-busy={busy || undefined}>
      {busy && <span className="spinner" aria-hidden />}{children}
    </button>
  );
}

export function LinkButton({ to, variant = "primary", children }: { to: string; variant?: string; children: ReactNode }) {
  return <Link to={to} className={`btn ${variant}`}>{children}</Link>;
}

export function Field({ label, hint, error, children, className = "" }: { label: string; hint?: ReactNode; error?: string; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={`field ${error ? "has-error" : ""} ${className}`}>
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {error ? <div className="field-error" role="alert">{error}</div> : hint ? <div className="field-hint">{hint}</div> : null}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as any;
  const list: string[] = e instanceof ApiError ? (e.body?.errors || Object.entries(e.fields).map(([k, v]) => `${k}: ${v}`)) : [];
  return (
    <div className="alert bad" role="alert">
      <strong>{e?.message || "Something went wrong"}</strong>
      {list.length > 0 && <ul>{list.map((x) => <li key={x}>{x}</li>)}</ul>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-title">{title}</div>{children && <div className="muted">{children}</div>}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="loading" role="status"><span className="spinner" aria-hidden /> {label}</div>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus();
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="Close">×</button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

type Toast = { id: number; tone: "good" | "bad" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, tone, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">{items.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}</div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "active" : ""} onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </div>
  );
}

/** Searchable select backed by a locally cached list (instant search, works on slow links). */
export function SearchSelect<T extends { id: string }>({ items, value, onChange, label, placeholder = "Search…", id, required }:
  { items: T[]; value: string; onChange: (id: string) => void; label: (x: T) => string; placeholder?: string; id?: string; required?: boolean }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const selected = items.find((x) => x.id === value);
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (s ? items.filter((x) => label(x).toLowerCase().includes(s)) : items).slice(0, 50);
  }, [q, items, label]);
  return (
    <div className="search-select">
      <input id={id} value={open ? q : selected ? label(selected) : ""} placeholder={placeholder} required={required && !value}
        onFocus={() => { setOpen(true); setQ(""); setHi(0); }} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => { setQ(e.target.value); setHi(0); }} autoComplete="off" role="combobox" aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, filtered.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          if (e.key === "Enter" && open && filtered[hi]) { e.preventDefault(); onChange(filtered[hi].id); setOpen(false); (e.target as HTMLInputElement).blur(); }
        }} />
      {open && (
        <ul className="ss-list" role="listbox">
          {filtered.length === 0 && <li className="muted">No matches</li>}
          {filtered.map((x, i) => (
            <li key={x.id} role="option" aria-selected={x.id === value} className={i === hi ? "hi" : ""}
              onMouseDown={() => { onChange(x.id); setOpen(false); }}>{label(x)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Confirm({ title, children, confirmLabel, onConfirm, onClose, danger, busy }:
  { title: string; children: ReactNode; confirmLabel: string; onConfirm: () => void; onClose: () => void; danger?: boolean; busy?: boolean }) {
  return (
    <Modal title={title} onClose={onClose}>
      {children}
      <div className="form-actions">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} busy={busy}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}
