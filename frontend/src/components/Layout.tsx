import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useConnection } from "../lib/connection";
import { dateTime } from "../lib/format";

const I = {
  dash: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  ing: "M12 3C7 3 4 7 4 11c0 5 4 10 8 10s8-5 8-10c0-4-3-8-8-8zm0 3c2.5 0 4 2 4 5h-8c0-3 1.5-5 4-5z",
  buy: "M7 4h-2l-1 2H2v2h1l3.6 7.6L5.2 18c-.7 1.3.3 3 1.8 3h12v-2H7l1.1-2h7.4c.8 0 1.4-.4 1.8-1l3.6-6.5c.4-.7-.1-1.5-.9-1.5H6.2L5.3 4H7z",
  sup: "M16 11c1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3 1.3 3 3 3zm-8 0c1.7 0 3-1.3 3-3S9.7 5 8 5 5 6.3 5 8s1.3 3 3 3zm0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13zm8 0c-.3 0-.6 0-1 .1 1.2.8 2 2 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5z",
  stock: "M20 7l-8-4-8 4v10l8 4 8-4V7zm-8 11.8L6 16V9.2l6 3v6.6zm1-8.4L7.2 7.5 12 5.1l4.8 2.4L13 10.4z",
  rep: "M5 9h3v10H5V9zm5.5-4h3v14h-3V5zM16 13h3v6h-3v-6z",
  set: "M19.4 13a7.6 7.6 0 000-2l2.1-1.6-2-3.4-2.5 1a7 7 0 00-1.7-1L15 3.3h-4L10.6 6a7 7 0 00-1.7 1l-2.5-1-2 3.4L6.6 11a7.6 7.6 0 000 2l-2.1 1.6 2 3.4 2.5-1c.5.4 1.1.8 1.7 1l.4 2.7h4l.4-2.7c.6-.2 1.2-.6 1.7-1l2.5 1 2-3.4-2.1-1.6zM13 15.5a3.5 3.5 0 110-7 3.5 3.5 0 010 7z",
};
const Icon = ({ d }: { d: string }) => <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d={d} /></svg>;

export const NAV = [
  { to: "/", label: "Dashboard", short: "Home", icon: I.dash, perm: "report.read", end: true },
  { to: "/ingredients", label: "Ingredients & Recipes", short: "Recipes", icon: I.ing, perm: "ingredient.read" },
  { to: "/purchases", label: "Purchases", short: "Buy", icon: I.buy, perm: "purchase.read" },
  { to: "/suppliers", label: "Suppliers", short: "Suppliers", icon: I.sup, perm: "supplier.read" },
  { to: "/stock", label: "Stock & Inventory", short: "Stock", icon: I.stock, perm: "stock.read" },
  { to: "/reports", label: "Reports", short: "Reports", icon: I.rep, perm: "report.read" },
];

export function SyncIndicator() {
  const c = useConnection();
  const pending = c.health?.sync_pending_events ?? 0;
  if (!c.serverReachable && !c.checking) {
    return (
      <span className="sync bad" title="This device cannot reach the restaurant server. Entries are kept as drafts on this device and nothing is posted until the connection returns.">
        <span className="dot" /> <span className="label">Server unreachable — drafts only</span>
      </span>
    );
  }
  return (
    <span className="sync ok" title={`Connected to the local branch server (works without internet). Last check: ${c.lastChecked ? dateTime(new Date(c.lastChecked).toISOString()) : "—"}.
Cloud sync is not enabled in this release: ${pending} change(s) are stored durably on the branch server, waiting to sync.`}>
      <span className="dot" /> <span className="label">Local server connected · {pending} awaiting cloud sync</span>
    </span>
  );
}

export function Layout() {
  const { me, can, logout } = useAuth();
  const nav = NAV.filter((n) => can(n.perm));
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand"><img src="/icon.svg" alt="" /> KitchenLedger</div>
        <nav className="nav">
          {nav.map((n) => <NavLink key={n.to} to={n.to} end={n.end}><Icon d={n.icon} />{n.label}</NavLink>)}
        </nav>
        <nav className="nav nav-bottom">
          <NavLink to="/settings"><Icon d={I.set} />Settings</NavLink>
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="row"><strong className="hide-sm">{me?.branch.name}</strong><SyncIndicator /></div>
          <div className="who">
            <span className="hide-sm muted small">{me?.user.display_name} · {me?.role}</span>
            {can("report.read") && <NavLink to="/reports" className="btn ghost sm show-sm">Reports</NavLink>}
            <NavLink to="/settings" className="btn ghost sm show-sm">Settings</NavLink>
            <button className="btn secondary sm" onClick={logout}>Sign out</button>
          </div>
        </header>
        <main className="content" id="main"><Outlet /></main>
      </div>
      <nav className="mobile-nav" aria-label="Main navigation">
        {nav.slice(0, 5).map((n) => <NavLink key={n.to} to={n.to} end={n.end}><Icon d={n.icon} />{n.short}</NavLink>)}
      </nav>
    </div>
  );
}
