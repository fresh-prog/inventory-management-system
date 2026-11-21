import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import CloudApp from "./cloud/CloudApp";
import { ToastProvider } from "./components/ui";
import { AuthProvider } from "./lib/auth";
import "./styles.css";

const qc = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, refetchOnWindowFocus: true, retry: (n, e: any) => n < 2 && !e?.status },
    mutations: { retry: false },
  },
});

// Same PWA for both deployment profiles: the restaurant server (branch) and the cloud console.
async function boot() {
  let profile = "branch";
  try {
    const r = await fetch("/api/v1/profile");
    if (r.ok) profile = (await r.json()).profile;
  } catch { /* server unreachable: open the branch app in drafts-only mode */ }
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={qc}>
        <BrowserRouter>
          <ToastProvider>
            {profile === "cloud" ? <CloudApp /> : <AuthProvider><App /></AuthProvider>}
          </ToastProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}
boot();

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
