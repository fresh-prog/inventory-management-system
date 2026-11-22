import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { get } from "./api";

export type Health = { status: string; database: string; disk: string; disk_free_percent: number; mode: string; version: string };
export type SyncStatus = { state: string; registered: boolean; last_success_at: string | null; last_error: string | null;
  halted_reason: string | null; pending_events: number; queue: { pending: number; retry: number; failed: number; files_pending: number } };

/** Cloud synchronisation details (authenticated; never exposed on the public health check). */
export function useSyncStatus(enabled = true) {
  return useQuery<SyncStatus>({ queryKey: ["sync-status"], queryFn: () => get("/sync/status"), refetchInterval: 15000,
                                retry: false, staleTime: 10000, enabled });
}

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
