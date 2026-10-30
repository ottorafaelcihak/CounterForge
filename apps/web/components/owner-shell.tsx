"use client";
import { UnsavedContext, confirmDiscard, useUnsavedNavigation } from "@/lib/unsaved";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode, type FormEvent } from "react";
export function OwnerShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null), tokenInput = useRef<HTMLInputElement>(null);
  const workspaceFocus = useRef<HTMLElement | null>(null);
  const [dirty, setDirty] = useState(false), [expired, setExpired] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  useUnsavedNavigation(dirty, setDirty);
  const [ready, setReady] = useState<boolean | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { fetch("/api/session", { cache: "no-store" }).then(r => r.json()).then(data => setReady(data.authenticated === true)).catch(() => setReady(false)); }, []);
  useEffect(() => { const expiredSession = () => setExpired(true); window.addEventListener("counterforge:unauthorized", expiredSession); return () => window.removeEventListener("counterforge:unauthorized", expiredSession); }, []);
  useEffect(() => {
    if (!expired || dismissed) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (previous?.closest("#workspace-content")) workspaceFocus.current = previous;
    const modal = dialog.current;
    modal?.showModal(); tokenInput.current?.focus();
    return () => {
      modal?.close();
      const fallback = document.querySelector<HTMLElement>('#workspace-content button:not([disabled]), #workspace-content input:not([disabled]), #workspace-content a[href]') || document.getElementById("workspace-content");
      (workspaceFocus.current?.isConnected ? workspaceFocus.current : previous?.isConnected ? previous : fallback)?.focus();
    };
  }, [expired, dismissed]);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget;
    const token = new FormData(form).get("token");
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error?.message || "Sign in failed.");
      form.reset(); setReady(true); setExpired(false); setDismissed(false); window.dispatchEvent(new Event("counterforge:authenticated"));
    } catch (e) { setError(e instanceof Error ? e.message : "Sign in failed."); }
    finally { setBusy(false); }
  }
  async function logout() {
    if (!confirmDiscard(dirty)) return;
    setDirty(false);
    const response = await fetch("/api/session", { method: "DELETE" });
    if (response.ok) { setReady(false); router.replace("/"); }
  }
  const loginForm = <form onSubmit={login} className="login-form"><h2 id="owner-login-title">{expired ? "Session expired" : "Owner sign in"}</h2>{expired && <p>Sign in again to keep your current unsaved changes and continue.</p>}<label>Owner access token<input ref={tokenInput} name="token" type="password" required autoComplete="current-password" maxLength={4096} /></label><p className="muted">Use the access token configured for this workspace.</p>{error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Signing in…" : "Open workspace"}</button>{expired && <button type="button" className="secondary" onClick={() => { dialog.current?.close(); setDismissed(true); }}>Continue without signing in</button>}</form>;
  return <>
    <a className="skip-link" href="#workspace-content">Skip to workspace</a>
    <header className="app-header"><Link href="/" className="brand"><span aria-hidden="true" className="brand-mark">◧</span> CounterForge</Link><span className="header-context">Visual test workspace</span>{ready && <button className="quiet" onClick={logout}>Sign out</button>}</header>
    {ready === null ? <main className="narrow"><p role="status">Opening workspace…</p></main> : ready ?
      <UnsavedContext.Provider value={{ dirty, setDirty }}><div key="authenticated">{expired && !dismissed && <dialog ref={dialog} className="session-overlay" aria-modal="true" aria-label="Renew owner session" onKeyDown={event => {
        if (event.key !== "Tab") return;
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input:not([disabled]), button:not([disabled]), a[href], [tabindex="0"]')].filter(control => control.getClientRects().length > 0);
        const first = controls[0], last = controls.at(-1);
        if (!first || !last) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === first || !event.currentTarget.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !event.currentTarget.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      }} onCancel={() => setDismissed(true)}>{loginForm}</dialog>}{expired && dismissed && <div className="review-warning" role="alert"><strong>Session expired.</strong> Your unsaved work is still on this page. <button type="button" className="secondary" onClick={() => setDismissed(false)}>Renew session</button></div>}<div key="workspace-content" id="workspace-content" tabIndex={-1}>{children}</div></div></UnsavedContext.Provider> :
      <main className="login-layout" id="workspace-content" tabIndex={-1}><div><span className="seed-glyph" aria-hidden="true">▥</span><h1>Start with a count you can trust.</h1><p>Set the ground truth, review every object, and preserve the evidence for repeatable visual tests.</p></div>{loginForm}</main>}
  </>;
}
