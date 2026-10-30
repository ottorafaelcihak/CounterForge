"use client";
/* eslint-disable @next/next/no-img-element -- Preserve authenticated evidence bytes without image optimization. */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, allPages, assetURL, type components } from "@/lib/client-api";
import { ExportCard, RunnerInstructions, fixtureLabel, usd } from "@/components/export-ui";

import { MinimizationPanel } from "./minimization-panel";

type Finding = components["schemas"]["Finding"];
type ExportRecord = components["schemas"]["ExportRef"];
const errorText = (e: unknown) => e instanceof Error ? e.message : "The request could not be completed.";
export function FindingDetail({ findingId }: { findingId: string }) {
  const [finding, setFinding] = useState<Finding | null>(null), [exports, setExports] = useState<ExportRecord[]>([]);
  const [loadError, setLoadError] = useState(""), [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const exportKey = useRef<string | null>(null), reload = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    let active = true, fetching = false;
    async function refresh() {
      if (!active || fetching) return;
      fetching = true;
      try {
        const [record, list] = await Promise.all([api<Finding>("findings/" + findingId), allPages<ExportRecord>("regressions?limit=100")]);
        if (active) { setFinding(record); setExports(list.items.filter(item => item.finding_id === findingId)); setLoadError(""); }
      } catch (e) { if (active) setLoadError(errorText(e)); }
      finally { fetching = false; }
    }
    reload.current = refresh; void refresh();
    const timer = window.setInterval(() => void refresh(), 5000), focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [findingId]);
  async function requestExport(newAttempt: boolean) {
    if (newAttempt || !exportKey.current) exportKey.current = crypto.randomUUID();
    setBusy(true); setActionError("");
    try {
      const record = await api<ExportRecord>("findings/" + findingId + "/exports", { method: "POST", headers: { "Idempotency-Key": exportKey.current } });
      setExports(previous => [record, ...previous.filter(item => item.export_id !== record.export_id)]);
      await reload.current();
    } catch (e) { setActionError(errorText(e)); }
    finally { setBusy(false); }
  }
  async function approveDemo() {
    setBusy(true); setActionError("");
    try { await api("findings/" + findingId + "/demo-approve", { method: "POST" }); await reload.current(); }
    catch (e) { setActionError(errorText(e)); } finally { setBusy(false); }
  }
  const evidence = finding?.evidence, usage = evidence?.usage;
  const fresh = evidence?.observations.filter(item => item.phase === "CONFIRMATION") || [];
  const pending = exports.some(item => item.state === "QUEUED" || item.state === "RUNNING");
  const ready = exports.some(item => item.state === "READY");
  const failed = exports.some(item => item.state === "FAILED");
  return <main className="workspace finding-workspace">
    <nav className="evidence-navigation"><Link href="/">Projects</Link><Link href="/regressions">Regression library</Link>{evidence && <Link href={"/experiments/" + evidence.experiment_id}>Experiment</Link>}<button className="quiet" onClick={() => reload.current()}>Reload evidence</button></nav>
    <div className="page-heading"><div><h1>Finding evidence</h1><p>Inspect the ground truth, exact evaluated images and fresh reproduction attempts.</p></div><span className="workspace-label">Owner workspace</span></div>
    {loadError && <p role="alert" className="error">{loadError}</p>}
    {!finding && !loadError && <p role="status">Loading retained evidence…</p>}
    {finding && <>
      <section className="form-panel"><div className="history-heading"><h2>Count {finding.ground_truth.value} · {finding.mutation.family}</h2><span className="badge">{finding.status}</span></div>
        {evidence?.provenance === "LOCAL_FIXTURE" && <p className="review-warning">{fixtureLabel}. Counts and observations demonstrate the pipeline; they do not establish a real model failure.</p>}
        <p>{finding.qualification}</p><p>Fresh confirmation: <strong>{finding.confirmation.incorrect}/{finding.confirmation.attempts}</strong> incorrect. Current validity: <strong>{evidence?.validation.verdict || finding.validation.verdict}</strong>.</p>
        <p>Mutation strength: {finding.mutation.strength}.</p><p>Model <code>{finding.model.model_id}</code> · revision <code>{finding.model.revision}</code></p>
        {finding.mutation_id && <Link href={"/mutations/" + finding.mutation_id}>Open candidate review and audit history</Link>}
      </section>
      {!evidence && <p className="review-warning">Retained detailed evidence requires review or repair. Open the candidate audit history; export remains unavailable.</p>}
      <MinimizationPanel findingId={findingId} eligible={!!evidence?.export_eligible} />
      {evidence && <>
        <section className="form-panel"><h2>Retained images</h2><div className="evidence-image-grid">{["original", "candidate", "diff", "protected_mask"].map(role => { const asset = evidence.assets[role]; return asset ? <figure key={role}><figcaption>{role.replaceAll("_", " ")}</figcaption>{/* Authenticated immutable evidence; native images preserve exact bytes. */}
          <img src={assetURL(asset.asset_id)} alt={role.replaceAll("_", " ") + " evidence"} /><details><summary>SHA-256</summary><code>{asset.sha256}</code></details></figure> : null; })}</div></section>
        <section className="form-panel"><h2>Validity and independent review</h2><p>{evidence.validation.confidence_note}</p><p>Verifier <code>{evidence.validation.verifier_version}</code> · policy <code>{evidence.validation.policy_version}</code></p>
          <ul className="evidence-checks">{evidence.validation.checks.map((check, index) => <li key={index}><strong>{check.code.replaceAll("_", " ")}: {check.status}</strong><div><p>{check.detail || (check.mandatory ? "Required invariant check" : "Supporting measurement")}</p><details><summary>Check measurements</summary><pre>{JSON.stringify(check, null, 2)}</pre></details></div></li>)}</ul>
          {evidence.validation.review_audit ? <><h3>Human review</h3><p>Reviewer: {evidence.validation.review_audit.actor}</p><p>{evidence.validation.review_audit.note}</p><p>Semantics: {evidence.validation.review_audit.semantic} · visibility: {evidence.validation.review_audit.visibility}</p></> : <p>No human override is recorded in the current report.</p>}
          <details><summary>Validation revisions ({evidence.validation_history.length})</summary>{evidence.validation_history.map(item => <p key={item.revision}>Revision {item.revision} · {item.created_at} · {item.report.verdict} · {item.report.review_audit?.actor || "automatic verifier"}</p>)}</details>
        </section>
        <section className="form-panel"><h2>Target configuration</h2><p className="evidence-prompt">{evidence.question}</p><pre>{JSON.stringify(evidence.sampling_options, null, 2)}</pre></section>
        <section className="form-panel"><h2>Target observations</h2><p>{fresh.length} retained fresh confirmation observations. Baseline and screening are listed separately below.</p>
          {evidence.observations.map(item => <article className="observation-card" key={item.id}><h3>{item.phase.replaceAll("_", " ")} · attempt {item.attempt_no}</h3><p>{item.outcome} · parsed count {item.parsed_count ?? "unavailable"} · {item.provenance} · {item.recorded_at}</p><pre className="raw-response">{item.raw_text || "(empty response)"}</pre><details><summary>Invocation evidence</summary><p>{item.model_id} · {item.model_revision} · {item.latency_ms ?? "unknown"} ms</p><code>{item.image_sha256}</code><p>{item.prompt}</p><pre>{JSON.stringify(item.sampling_options, null, 2)}</pre></details></article>)}
        </section>
        {usage && <section className="form-panel"><h2>Experiment API usage</h2><p>Includes baseline, planning, generation, validation, screening and confirmation across this experiment.</p><p>Provider requests: {usage.calls} total · {usage.target_calls} target · input tokens {usage.known_tokens_in} · output tokens {usage.known_tokens_out}{usage.unknown_token_calls ? " · " + usage.unknown_token_calls + " calls have unknown token usage" : ""}.</p><dl className="metrics-grid"><div><dt>Actual</dt><dd>{usd(usage.actual_usd)}</dd></div><div><dt>Estimated</dt><dd>{usd(usage.estimated_usd)}</dd></div><div><dt>Unresolved</dt><dd>{usd(usage.unresolved_usd)}</dd></div><div><dt>Reserved</dt><dd>{usd(usage.reserved_usd)}</dd></div></dl></section>}
        <section className="form-panel"><h2>Export and showcase</h2><p>{evidence.qualification}</p>
          {!evidence.export_eligible && <p className="review-warning">Export unavailable: {evidence.export_blocked_reasons.join(", ") || "Current evidence does not meet the stable export requirements."}</p>}
          {actionError && <p className="error" role="alert">{actionError} Retrying the same request preserves its idempotency key.</p>}
          <div className="actions"><button className="primary" data-testid="request-export" disabled={busy || pending || ready || failed || !evidence.export_eligible} onClick={() => requestExport(false)}>{busy ? "Requesting…" : pending ? "Export in progress" : ready ? "Verified export available" : failed ? "Export failed; start a new attempt" : "Request regression export"}</button>
          {failed && !pending && !ready && <button disabled={busy || !evidence.export_eligible} onClick={() => requestExport(true)}>Retry failed export as a new attempt</button>}
          <button disabled={busy || finding.demo_approved || !evidence.export_eligible} onClick={approveDemo}>{finding.demo_approved ? "Approved for showcase" : "Approve finding for showcase"}</button></div><p className="muted">Showcase approval is a separate human decision. Exporting does not grant it.</p>
        </section>
      </>}
      <section className="export-list" aria-label="Finding exports">{exports.map(record => <ExportCard key={record.export_id} record={record} verificationAllowed={!!evidence?.export_eligible} />)}</section>
      <RunnerInstructions />
    </>}
  </main>;
}
