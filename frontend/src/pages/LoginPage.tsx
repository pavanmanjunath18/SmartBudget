import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { DEMO_EMAIL, DEMO_PASSWORD } from "../demo";

export function LoginPage() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(() => (mode === "login" ? login(email, password) : signup(email, password, orgName)));
  };

  return (
    <div className="login">
      <h1>SmartBudget</h1>
      <p className="muted">Bookkeeping for freelancers and small businesses.</p>

      <div className="panel demo-box">
        <h2>Just looking? Try the demo</h2>
        <p>
          A shared demo account with three months of sample bookkeeping already loaded: a bank
          statement, invoices, and reports. You can also download sample CSV files, import them,
          and watch the dashboard and reports change.
        </p>
        <p className="credentials">
          Email <code>{DEMO_EMAIL}</code>
          <br />
          Password <code>{DEMO_PASSWORD}</code>
        </p>
        <button
          className="primary"
          disabled={busy}
          onClick={() => void run(() => login(DEMO_EMAIL, DEMO_PASSWORD))}
        >
          Open the demo
        </button>
      </div>

      <div className="panel">
        <h2>{mode === "login" ? "Or log in to your own account" : "Create your own account"}</h2>
        <form onSubmit={submit}>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={mode === "signup" ? 8 : undefined}
              required
            />
          </label>
          {mode === "signup" && (
            <label>
              Business name
              <input value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
            </label>
          )}
          <button className="primary" disabled={busy}>
            {mode === "login" ? "Log in" : "Sign up"}
          </button>
        </form>
        <p className="muted">
          {mode === "login" ? "New here? " : "Have an account? "}
          <button
            className="link"
            onClick={() => setMode(mode === "login" ? "signup" : "login")}
          >
            {mode === "login" ? "Create an account" : "Log in"}
          </button>
        </p>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
