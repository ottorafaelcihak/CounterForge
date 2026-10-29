"use client";
import { UnsavedContext, confirmDiscard, useUnsavedNavigation } from "@/lib/unsaved";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode, type FormEvent } from "react";
export function OwnerShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [dirty, setDirty] = useState(false), [expired, setExpired] = useState(false);
  useUnsavedNavigation(dirty, setDirty);
  const [ready, setReady] = useState<boolean | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { fetch("/api/session", { cache: "no-store" }).then(r => r.json()).then(data => setReady(data.authenticated === true)).catch(() => setReady(false)); }, []);
  useEffect(() => { const expiredSession = () => setExpired(true); window.addEventListener("counterforge:unauthorized", expiredSession); return () => window.removeEventListener("counterforge:unauthorized", expiredSession); }, []);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget;
    const token = new FormData(form).get("token");
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Sign in failed.");
      form.reset(); setReady(true); setExpired(false);
    } catch (e) { setError(e instanceof Error ? e.message : "Sign in failed."); }
    finally { setBusy(false); }
  }
  async function logout() {
    if (!confirmDiscard(dirty)) return;
    setDirty(false);
    const response = await fetch("/api/session", { method: "DELETE" });
    if (response.ok) { setReady(false); router.replace("/"); }
  }
  const loginForm = <form onSubmit={login} className="login-form"><h2>{expired ? "Session expired" : "Owner sign in"}</h2>{expired && <p>Sign in again to keep your current unsaved changes and continue.</p>}<label>Owner access token<input name="token" type="password" required autoComplete="current-password" maxLength={4096} /></label><p className="muted">Use the access token configured for this workspace.</p>{error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Signing in…" : "Open workspace"}</button></form>;
  return <>
    <header className="app-header"><Link href="/" className="brand"><span aria-hidden="true" className="brand-mark">◧</span> CounterForge</Link><span className="header-context">Visual test workspace</span>{ready && <button className="quiet" onClick={logout}>Sign out</button>}</header>
    {ready === null ? <main className="narrow"><p role="status">Opening workspace…</p></main> : ready ?
      <UnsavedContext.Provider value={{ dirty, setDirty }}><div key="authenticated">{expired && <div className="session-overlay" role="dialog" aria-modal="true" aria-label="Renew owner session">{loginForm}</div>}<div key="workspace-content">{children}</div></div></UnsavedContext.Provider> :
      <main className="login-layout"><div><span className="seed-glyph" aria-hidden="true">▥</span><h1>Start with a count you can trust.</h1><p>Set the ground truth, review every object, and preserve the evidence for repeatable visual tests.</p></div>{loginForm}</main>}
  </>;
}
