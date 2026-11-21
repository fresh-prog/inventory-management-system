import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export type Health = { status: string; database: string; disk: string; disk_free_percent: number; mode: string;
  sync_pending_events: number; sync_failed_events: number; cloud_registered: boolean;
  cloud_last_success_at: string | null; cloud_last_error: string | null; sync_halted: string | null };

/** Connection to the LOCAL branch server (not the internet). The app works without internet. */
export function useConnection() {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  const q = useQuery<Health>({
    queryKey: ["health"],
    queryFn: async () => {
      const r = await fetch("/api/v1/health/ready");
      if (!r.ok && r.status !== 503) throw new Error("unreachable");
      return r.json();
    },
    refetchInterval: 15000, retry: false, staleTime: 10000,
  });
  const serverReachable = q.isSuccess && !q.isError && q.data?.database === "ok";
  return { deviceNetwork: online, serverReachable, checking: q.isLoading, health: q.data, lastChecked: q.dataUpdatedAt };
}
