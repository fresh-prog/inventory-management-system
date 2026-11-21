// Thin API client. All business calculations happen on the server; the UI only displays them.
const TOKEN_KEY = "kl.token";
const DEVICE_KEY = "kl.device";

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

export const device = {
  get: () => { try { return localStorage.getItem(DEVICE_KEY); } catch { return null; } },
  set: (d: string | null) => {
    try { d ? localStorage.setItem(DEVICE_KEY, d) : localStorage.removeItem(DEVICE_KEY); } catch { /* ignore */ }
  },
};

export function deviceName(): string {
  const ua = navigator.userAgent;
  const kind = /iPad|Tablet/i.test(ua) ? "Tablet" : /Mobi|Android|iPhone/i.test(ua) ? "Phone" : "Computer";
  const os = /Android/i.test(ua) ? "Android" : /iPhone|iPad|Mac OS/i.test(ua) ? "Apple" : /Windows/i.test(ua) ? "Windows" : /Linux/i.test(ua) ? "Linux" : "";
  return `${kind}${os ? " (" + os + ")" : ""}`;
}

let onUnauthorized: () => void = () => {};
let onDeviceProblem: (msg: string) => void = () => {};
export const setDeviceProblemHandler = (fn: (msg: string) => void) => { onDeviceProblem = fn; };
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
  const d = device.get();
  if (d) headers["X-Device-Id"] = d;
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
    if (res.status === 403 && (body?.code_hint === "DEVICE_REVOKED" || body?.code_hint === "DEVICE_UNKNOWN")) {
      device.set(null);
      onDeviceProblem(body.detail);
    }
    throw new ApiError(res.status, body);
  }
  return body as T;
}

export const get = <T = any>(path: string) => api<T>(path);

/** Fetch a protected file (receipt) and open it; needs the auth header so a plain link won't do. */
export async function openFile(fileId: string) {
  const headers: Record<string, string> = {};
  const t = token.get(); if (t) headers.Authorization = `Bearer ${t}`;
  const res = await fetch(`/api/v1/files/${fileId}`, { headers });
  if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => ({})));
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function uploadFile(path: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = { Accept: "application/json" };
  const t = token.get(); if (t) headers.Authorization = `Bearer ${t}`;
  const d = device.get(); if (d) headers["X-Device-Id"] = d;
  let res: Response;
  try { res = await fetch(`/api/v1${path}`, { method: "POST", headers, body: form }); } catch { throw new NetworkError(); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body);
  return body;
}
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
