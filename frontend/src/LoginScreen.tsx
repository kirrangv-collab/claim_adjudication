import { useState } from "react";
import { CircleAlert, HeartPulse, LoaderCircle, Lock, ShieldCheck, User } from "lucide-react";

export function LoginScreen({
  onLogin,
}: {
  onLogin: (username: string, password: string) => Promise<void>;
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
      setError(cause instanceof Error ? cause.message : "Unable to sign in.");
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

        <div className="login-footer"><ShieldCheck size={13} /> Research prototype — every adjudication requires independent human sign-off.</div>
      </div>
    </div>
  );
}
