// Thin API client. All business calculations happen on the server; the UI only displays them.
const TOKEN_KEY = "kl.token";

export class ApiError extends Error {
  status: number;
  code: string;
  fields: Record<string, string>;
  body: any;
  constructor(status: number, body: any) {
    super(body?.detail || body?.title || `Request failed (${status})`);
    this.status = status;
    this.code = body?.code || "ERROR";
    this.fields = body?.fields || {};
    this.body = body;
  }
}

export class NetworkError extends Error {
  constructor() {
    super("Cannot reach the restaurant server. Check the Wi-Fi/network connection.");
  }
}

export const token = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t: string | null) => {
    try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
  },
};

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export function newKey(): string {
  return crypto.randomUUID ? crypto.randomUUID()
    : "k-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

type Opts = { method?: string; body?: unknown; idempotencyKey?: string; headers?: Record<string, string> };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  const t = token.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new NetworkError();
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && t) onUnauthorized();
    throw new ApiError(res.status, body);
  }
  return body as T;
}

export const get = <T = any>(path: string) => api<T>(path);
export const post = <T = any>(path: string, body?: unknown, idempotencyKey?: string) =>
  api<T>(path, { method: "POST", body: body ?? {}, idempotencyKey });
export const put = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const patch = <T = any>(path: string, body: unknown, headers?: Record<string, string>) =>
  api<T>(path, { method: "PATCH", body, headers });

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
