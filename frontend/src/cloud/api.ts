import { ApiError, NetworkError } from "../lib/api";

const KEY = "kl.cloud.token";
export const cloudToken = {
  get: () => { try { return localStorage.getItem(KEY); } catch { return null; } },
  set: (t: string | null) => { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch { /* ignore */ } },
};

export async function capi<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const t = cloudToken.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/api/v1/cloud${path}`, { method: method || (body !== undefined ? "POST" : "GET"), headers,
                                               body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch { throw new NetworkError(); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && t && !path.startsWith("/auth")) { cloudToken.set(null); location.reload(); }
    throw new ApiError(res.status, data);
  }
  return data as T;
}
