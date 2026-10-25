import { useState } from "react";
import { CircleAlert, FlaskConical, HeartPulse, LoaderCircle, Lock, ShieldCheck, User } from "lucide-react";

export type PublicDemo = {
  accounts: { username: string; role: string }[];
  password: string;
  notice: string;
};

const DEMO_ROLE_HINT: Record<string, string> = {
  "demo-maker": "submits cases",
  "demo-checker": "records decisions",
};

export function LoginScreen({
  onLogin,
  demo,
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  demo?: PublicDemo | null;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onLogin(username, password);
    } catch (cause) {
      setError(
        cause instanceof TypeError
          ? "Could not reach the API. Check that the backend is running and reachable, then try again."
          : cause instanceof Error ? cause.message : "Unable to sign in.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-icon"><HeartPulse size={22} strokeWidth={2.2} /></span>
          <div><strong>claim<span>lab</span></strong><small>RESEARCH WORKSPACE</small></div>
        </div>
        <h1>Reviewer sign-in</h1>
        <p>Accounts are provisioned by an administrator. Self-registration is disabled by design.</p>

        {demo && (
          <div className="demo-login">
            <div className="demo-login-heading"><FlaskConical size={14} /> Public demo — try it without an account</div>
            <div className="demo-login-accounts">
              {demo.accounts.map((account) => (
                <button
                  key={account.username}
                  type="button"
                  className="button secondary"
                  onClick={() => { setUsername(account.username); setPassword(demo.password); setError(""); }}
                >
                  Use <b>{account.username}</b> <small>({DEMO_ROLE_HINT[account.username] ?? account.role})</small>
                </button>
              ))}
            </div>
            <small>Password for both: <code>{demo.password}</code>. {demo.notice}</small>
          </div>
        )}

        {error && <div className="error-banner" role="alert"><CircleAlert size={16} /><span>{error}</span></div>}

        <form onSubmit={handleSubmit}>
          <label className="login-field">
            <span><User size={14} /> Username</span>
            <input
              required
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. jsmith"
            />
          </label>
          <label className="login-field">
            <span><Lock size={14} /> Password</span>
            <input
              required
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
            />
          </label>
          <button className="button primary login-submit" type="submit" disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={16} /> : null}
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="login-footer"><ShieldCheck size={13} /> Research prototype — only high-confidence cases are auto-finalised; all others require independent human sign-off.</div>
      </div>
    </div>
  );
}
