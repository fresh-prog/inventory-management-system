import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NetworkError, get, post, setUnauthorizedHandler, token } from "./api";
import { configureMoney } from "./format";

export type Me = {
  user: { id: string; email: string; display_name: string };
  role: string;
  permissions: string[];
  branch: { id: string; code: string; name: string; country: string; currency: string; currency_decimals: number;
            default_target_food_cost_rate: string; include_tax_in_cost: boolean };
};

type Ctx = { me: Me | null; loading: boolean; can: (p: string) => boolean;
             login: (email: string, password: string) => Promise<void>; logout: () => void; reload: () => Promise<void> };
const AuthCtx = createContext<Ctx>(null as any);
const ME_KEY = "kl.me";
// The signed-in profile is cached on the device so the app still opens (in "drafts only"
// mode) when the branch server is briefly unreachable. The server re-checks every request.
const cache = {
  get: (): Me | null => { try { return JSON.parse(localStorage.getItem(ME_KEY) || "null"); } catch { return null; } },
  set: (m: Me | null) => { try { m ? localStorage.setItem(ME_KEY, JSON.stringify(m)) : localStorage.removeItem(ME_KEY); } catch { /* ignore */ } },
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const reload = useCallback(async () => {
    if (!token.get()) { setMe(null); setLoading(false); return; }
    try {
      const m = await get<Me>("/me");
      configureMoney(m.branch.currency, m.branch.currency_decimals);
      cache.set(m);
      setMe(m);
    } catch (e: any) {
      const cached = cache.get();
      if (e instanceof NetworkError && cached) {
        configureMoney(cached.branch.currency, cached.branch.currency_decimals);
        setMe(cached);
      } else {
        if (e?.status === 401) token.set(null);
        cache.set(null);
        setMe(null);
      }
    } finally { setLoading(false); }
  }, []);

  const logout = useCallback(() => { token.set(null); cache.set(null); setMe(null); qc.clear(); }, [qc]);

  useEffect(() => { setUnauthorizedHandler(logout); reload(); }, [reload, logout]);

  const login = async (email: string, password: string) => {
    const r = await post<{ access_token: string }>("/auth/login", { email, password });
    token.set(r.access_token);
    await reload();
  };
  const can = (p: string) => !!me?.permissions.includes(p);
  return <AuthCtx.Provider value={{ me, loading, can, login, logout, reload }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
