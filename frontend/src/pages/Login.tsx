import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorBox, Field } from "../components/ui";
import { get, post } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const status = useQuery({ queryKey: ["setup-status"], queryFn: () => get<{ needs_setup: boolean }>("/setup/status") });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [setup, setSetup] = useState({ branch_code: "", branch_name: "", country: "UG", currency: "UGX", currency_decimals: 0,
                                       owner_name: "", owner_email: "", owner_password: "" });

  if (status.data?.needs_setup) {
    const submit = async (e: React.FormEvent) => {
      e.preventDefault(); setBusy(true); setError(null);
      try {
        await post("/setup", setup);
        await login(setup.owner_email, setup.owner_password);
      } catch (err) { setError(err); } finally { setBusy(false); }
    };
    const s = (k: keyof typeof setup) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setSetup({ ...setup, [k]: k === "currency_decimals" ? Number(e.target.value) : e.target.value });
    return (
      <div className="auth-wrap">
        <form className="card auth-card" onSubmit={submit}>
          <div className="brand"><img src="/icon.svg" alt="" width={30} /> KitchenLedger</div>
          <h1>Set up your restaurant</h1>
          <p className="muted">This runs once on the restaurant server. You become the owner.</p>
          <ErrorBox error={error} />
          <div className="form-grid">
            <Field label="Branch code">{(id) => <input id={id} required value={setup.branch_code} onChange={s("branch_code")} placeholder="KLA" />}</Field>
            <Field label="Branch name">{(id) => <input id={id} required value={setup.branch_name} onChange={s("branch_name")} placeholder="Kampala Central" />}</Field>
            <Field label="Country">{(id) => <select id={id} value={setup.country} onChange={s("country")}><option value="UG">Uganda</option><option value="ER">Eritrea</option><option value="KE">Kenya</option><option value="US">Other</option></select>}</Field>
            <Field label="Currency">{(id) => <input id={id} required maxLength={3} value={setup.currency} onChange={s("currency")} />}</Field>
            <Field label="Currency decimals" hint="UGX 0 · ERN 2 · USD 2">{(id) => <input id={id} type="number" min={0} max={4} value={setup.currency_decimals} onChange={s("currency_decimals")} />}</Field>
            <Field label="Your name">{(id) => <input id={id} required value={setup.owner_name} onChange={s("owner_name")} />}</Field>
            <Field label="Email">{(id) => <input id={id} type="email" required value={setup.owner_email} onChange={s("owner_email")} />}</Field>
            <Field label="Password" hint="At least 10 characters">{(id) => <input id={id} type="password" required minLength={10} value={setup.owner_password} onChange={s("owner_password")} />}</Field>
          </div>
          <div className="form-actions"><Button busy={busy} type="submit">Create restaurant</Button></div>
        </form>
      </div>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try { await login(email, password); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand"><img src="/icon.svg" alt="" width={30} /> KitchenLedger</div>
        <h1>Sign in</h1>
        <p className="muted">Ingredient costing, purchasing and stock — on your restaurant's own server.</p>
        <ErrorBox error={error} />
        <div className="stack">
          <Field label="Email">{(id) => <input id={id} type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Password">{(id) => <input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
        </div>
        <div className="form-actions"><Button busy={busy} type="submit" style={{ width: "100%" }}>Sign in</Button></div>
      </form>
    </div>
  );
}
