import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL ?? "";
const STORAGE_KEY = "claimlab_session";

export type Session = { token: string; username: string; role: string };

function readStoredSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.token === "string") return parsed as Session;
  } catch {
    // Corrupt or inaccessible storage — treat as logged out.
  }
  return null;
}

/**
 * Bearer-token auth stored in sessionStorage (cleared when the tab closes).
 * This is a common, well-understood SPA pattern, but it is JS-readable and
 * therefore more exposed to token theft via XSS than an httpOnly cookie
 * would be. A hardened deployment should consider httpOnly/secure cookies
 * or a managed OIDC provider instead of rolling token storage further.
 */
export function useAuth() {
  const [session, setSession] = useState<Session | null>(() => readStoredSession());

  useEffect(() => {
    if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(STORAGE_KEY);
  }, [session]);

  const login = useCallback(async (username: string, password: string) => {
    const body = new URLSearchParams({ username, password });
    const response = await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(
        typeof detail?.detail === "string" ? detail.detail : "Invalid username or password."
      );
    }
    const data = await response.json();
    setSession({ token: data.access_token, username, role: data.role });
  }, []);

  const logout = useCallback(() => setSession(null), []);

  const authFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      if (session) headers.set("Authorization", `Bearer ${session.token}`);
      const response = await fetch(`${API}${path}`, { ...init, headers });
      if (response.status === 401) {
        setSession(null);
      }
      return response;
    },
    [session]
  );

  return { session, login, logout, authFetch };
}
