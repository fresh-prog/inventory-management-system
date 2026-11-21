// Display formatting only. Values arrive from the API as exact decimal strings.
export type Dec = string | number | null | undefined;

let currency = "USD";
let decimals = 2;
export function configureMoney(c: string, d: number) { currency = c; decimals = d; }
export const currencyCode = () => currency;

export function num(v: Dec): number {
  if (v === null || v === undefined || v === "") return NaN;
  return typeof v === "number" ? v : Number(v);
}

export function money(v: Dec, opts: { decimals?: number; sign?: boolean } = {}): string {
  const n = num(v);
  if (Number.isNaN(n)) return "—";
  const d = opts.decimals ?? decimals;
  const s = new Intl.NumberFormat(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }).format(Math.abs(n));
  const sign = n < 0 ? "−" : opts.sign && n > 0 ? "+" : "";
  return `${sign}${currency} ${s}`;
}

/** Small unit costs (e.g. per gram) need more precision than invoice totals. */
export function unitCost(v: Dec, perUnit: string): string {
  const n = num(v);
  if (Number.isNaN(n)) return "—";
  const d = n === 0 ? decimals : Math.max(decimals, Math.min(6, Math.ceil(-Math.log10(Math.abs(n))) + 2));
  return `${money(n, { decimals: d })} / ${perUnit}`;
}

export function qty(v: Dec, unit?: string, maxDecimals = 3): string {
  const n = num(v);
  if (Number.isNaN(n)) return "—";
  const s = new Intl.NumberFormat(undefined, { maximumFractionDigits: maxDecimals }).format(n);
  return unit ? `${s} ${unit}` : s;
}

/** Show base-unit quantities in a friendlier unit (g -> kg, ml -> l) when large. */
export function smartQty(v: Dec, baseUnit: string): string {
  const n = num(v);
  if (Number.isNaN(n)) return "—";
  if (baseUnit === "g" && Math.abs(n) >= 1000) return qty(n / 1000, "kg");
  if (baseUnit === "ml" && Math.abs(n) >= 1000) return qty(n / 1000, "l");
  return qty(n, baseUnit);
}

export function pct(v: Dec, d = 1): string {
  const n = num(v);
  return Number.isNaN(n) ? "—" : `${(n * 100).toFixed(d)}%`;
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const dt = new Date(v.length === 10 ? v + "T00:00:00" : v);
  return dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const today = () => new Date().toISOString().slice(0, 10);

/** Exact-ish decimal multiply for live UI previews (server recomputes authoritatively). */
export function mul(a: Dec, b: Dec): number {
  const x = num(a), y = num(b);
  return Number.isNaN(x) || Number.isNaN(y) ? 0 : Math.round(x * y * 10000) / 10000;
}

export const UNITS = ["g", "kg", "ml", "l", "unit"] as const;
export const DIMENSION_UNITS: Record<string, string[]> = {
  mass: ["g", "kg"], volume: ["ml", "l"], count: ["unit"],
};
export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
