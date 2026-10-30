"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, allPages, jsonPost, type components } from "@/lib/client-api";
import { JobCosts, actionError } from "./refinement-shared";

type Job = components["schemas"]["JobRef"];
type Target = components["schemas"]["Target"];
type Capabilities = components["schemas"]["SetupCapabilities"];
type Request = components["schemas"]["RetestRequest"];
type Comparison = NonNullable<NonNullable<Job["result"]>["retest"]>;

function ComparisonResult({ result }: { result: Comparison }) {
  const a = result.target_a, b = result.target_b;
  return <section data-testid="retest-result" data-classification={result.classification}>
    <h4>{result.classification}</h4>
    <p>Expected count {result.expected_count}. Fresh coverage {result.completed_attempts}/{result.planned_attempts}; {result.interpretable_attempts} interpretable replies.</p>
    <p>Incorrect among interpretable replies: {result.incorrect}/{result.interpretable_attempts}. Uncertain replies: {result.unknown}/{result.completed_attempts}; provider errors: {result.errors}/{result.completed_attempts}. A zero denominator has no estimated rate.</p>
    <dl className="metrics-grid"><div><dt>Correct</dt><dd>{result.correct}</dd></div><div><dt>Incorrect</dt><dd>{result.incorrect}</dd></div><div><dt>Unknown</dt><dd>{result.unknown}</dd></div><div><dt>Provider errors</dt><dd>{result.errors}</dd></div></dl>
    <p>Historic target A: {result.historical_screening_incorrect} separate incorrect screen and {result.historical_fresh_incorrect}/{result.historical_fresh_attempts} fresh confirmations incorrect.</p>
    <div className="comparison-targets"><article><h5>Historic target A</h5><p>{a.model_id} · configured revision {a.configured_revision}</p><p>Observed: {(a.observed_revisions || []).join(", ") || "Not observed"}</p><pre>{JSON.stringify(a.inference_options, null, 2)}</pre></article><article><h5>Fresh target B</h5><p>{b.model_id} · configured revision {b.configured_revision}</p><p>Observed: {(b.observed_revisions || []).join(", ") || "Not observed"}</p><pre>{JSON.stringify(b.inference_options, null, 2)}</pre></article></div>
    {!!result.warnings?.length && <p className="review-warning">{result.warnings.map(value => value.replaceAll("_", " ")).join("; ")}</p>}
    {result.stop_reason && <p>Stopped: {result.stop_reason.replaceAll("_", " ")}</p>}
    <p className="muted">{result.qualification}</p>
    <details><summary>Exact frozen input and bundle</summary><dl className="retest-hashes"><dt>Input SHA-256</dt><dd><code>{result.input_sha256}</code></dd><dt>Prompt SHA-256</dt><dd><code>{result.prompt_sha256}</code></dd><dt>Bundle SHA-256</dt><dd><code>{result.bundle_sha256}</code></dd><dt>Manifest SHA-256</dt><dd><code>{result.manifest_sha256}</code></dd></dl></details>
    <p><Link href={"/findings/" + result.source_finding_id}>Original target-A finding and observations</Link></p>
  </section>;
}

export function RetestPanel({ exportId, projectId, verified }: { exportId: string; projectId: string; verified: boolean }) {
  const [open, setOpen] = useState(false), [jobs, setJobs] = useState<Job[]>([]), [targets, setTargets] = useState<Target[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null), [targetId, setTargetId] = useState("");
  const [limits, setLimits] = useState({ max_calls: 3, max_estimated_usd: 5, max_runtime_seconds: 1200 });
  const [configError, setConfigError] = useState("");
  const [error, setError] = useState(""), [loadError, setLoadError] = useState(""), [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false);
  const key = useRef<{ body: string; value: string } | null>(null), refresh = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    if (!open) return;
    let active = true;
    void Promise.all([allPages<Target>("projects/" + projectId + "/targets?limit=100"), api<Capabilities>("setup")]).then(([list, config]) => {
      if (!active) return;
      const enabled = list.items.filter(target => target.enabled);
      setTargets(enabled); setCapabilities(config); setConfigError("");
      setTargetId(old => enabled.some(target => target.id === old) ? old : enabled[0]?.id || "");
    }).catch(value => { if (active) setConfigError(actionError(value)); });
    return () => { active = false; };
  }, [open, projectId]);
  useEffect(() => {
    if (!open) return;
    let active = true, fetching = false;
    async function load() {
      if (!active || fetching) return;
      fetching = true;
      try {
        const list = await allPages<Job>("regressions/" + exportId + "/retests?limit=100");
        if (active) { setJobs(list.items); setLoaded(true); setLoadError(""); }
      } catch (value) { if (active) setLoadError(actionError(value)); }
      finally { fetching = false; }
    }
    refresh.current = load; void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, exportId]);
  const valid = Number.isInteger(limits.max_calls) && limits.max_calls >= 1 && limits.max_calls <= 3 && Number.isFinite(limits.max_estimated_usd) && limits.max_estimated_usd > 0 && limits.max_estimated_usd <= 150 && Number.isInteger(limits.max_runtime_seconds) && limits.max_runtime_seconds >= 60 && limits.max_runtime_seconds <= 7200;
  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !verified || !targetId || !valid || !capabilities || capabilities.provider_mode === "DISABLED") return;
    const body: Request = { target_config_id: targetId, limits }, canonical = JSON.stringify(body);
    if (!key.current || key.current.body !== canonical) key.current = { body: canonical, value: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      const job = await api<Job>("regressions/" + exportId + "/retest", { ...jsonPost(body), headers: { "Content-Type": "application/json", "Idempotency-Key": key.current.value } });
      setJobs(previous => [job, ...previous.filter(item => item.job_id !== job.job_id)]);
      key.current = null; await refresh.current();
    } catch (value) { setError(actionError(value)); }
    finally { setBusy(false); }
  }
  return <details className="retest-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Compare a target configuration · fresh retest</summary>
    {open && <><p>Each retest is a new job using the exact bundle image, prompt and human count. Three fresh interpretable replies are required for a complete comparison. Original evidence remains retained.</p>
      {!verified && <p className="review-warning">Verify this bundle before starting a fresh retest.</p>}
      {configError && <p role="alert" className="error">{configError}</p>}
      {loadError && <p role="alert" className="error">{loadError}</p>}
      <form onSubmit={start} className="retest-form"><label>Target B<select aria-label="Retest target B" value={targetId} onChange={event => setTargetId(event.target.value)} disabled={busy} required><option value="">Choose an authorized target</option>{targets.map(target => <option key={target.id} value={target.id}>{target.model_id} · {target.model_revision} · {target.id.slice(0, 8)}</option>)}</select></label>
        <fieldset className="limit-fields"><legend>Explicit allowance for this new retest</legend><label>Calls<input aria-label="Maximum retest calls" type="number" min={1} max={3} step={1} value={limits.max_calls} onChange={event => setLimits(old => ({ ...old, max_calls: Number(event.target.value) }))} disabled={busy} required /></label><label>Dollars<input aria-label="Maximum retest dollars" type="number" min={0.001} max={150} step={0.001} value={limits.max_estimated_usd} onChange={event => setLimits(old => ({ ...old, max_estimated_usd: Number(event.target.value) }))} disabled={busy} required /></label><label>Seconds<input aria-label="Maximum retest seconds" type="number" min={60} max={7200} step={1} value={limits.max_runtime_seconds} onChange={event => setLimits(old => ({ ...old, max_runtime_seconds: Number(event.target.value) }))} disabled={busy} required /></label></fieldset>
        <p className={capabilities?.provider_mode === "LIVE" ? "review-warning" : "muted"}>{capabilities?.provider_mode === "LIVE" ? "Starting sends paid Meta requests within this allowance and the deployment ceiling. Uncertain charges remain counted." : capabilities?.provider_mode === "FIXTURE" ? "LOCAL_FIXTURE · simulated comparison; no paid provider calls." : "Provider dispatch is unavailable."}</p>
        <p className="muted">An allowance below three completed calls produces an inconclusive comparison. Revision labels and sampling changes do not guarantee a separately pinned provider model.</p>
        {error && <p role="alert" className="error">{error} Retry the unchanged form to preserve its request identity.</p>}
        <button className="secondary" data-testid="start-retest" disabled={busy || !verified || !targetId || !valid || !capabilities || capabilities.provider_mode === "DISABLED"}>{busy ? "Requesting…" : "Start new bounded retest"}</button>
      </form>
      <section aria-label="Retained retest jobs">{!loaded ? <p role="status">Loading retests…</p> : !jobs.length ? <p>No retest job has been recorded.</p> : jobs.map(job => <article className="retest-job" data-testid="retest-job" data-job-id={job.job_id} key={job.job_id}><h4>Retest {job.job_id.slice(0, 8)} · {job.state}</h4>{job.created_at && <p>Requested {new Date(job.created_at).toLocaleString()}{job.finished_at && <> · finished {new Date(job.finished_at).toLocaleString()}</>}</p>}{job.result?.synthetic && <p className="review-warning">LOCAL_FIXTURE · simulated evidence</p>}{job.error_code && <p>Code: {job.error_code}</p>}{job.result?.retest ? <ComparisonResult result={job.result.retest} /> : <p role="status">{job.state === "QUEUED" || job.state === "RUNNING" ? "Waiting for fresh results…" : "No complete comparison is available."}</p>}<JobCosts usage={job.usage} /></article>)}</section>
    </>}
  </details>;
}
