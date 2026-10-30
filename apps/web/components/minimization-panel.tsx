"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, allPages, jsonPost, type components } from "@/lib/client-api";
import { JobCosts, actionError } from "./refinement-shared";

type Job = components["schemas"]["JobRef"];
type Request = components["schemas"]["MinimizeRequest"];
type Result = NonNullable<NonNullable<Job["result"]>["minimization"]>;

function ReductionResult({ result }: { result: Result }) {
  const improved = result.best_finding_id !== result.source_finding_id;
  return <div data-testid="minimization-result" data-stop-reason={result.stop_reason}>
    <p><strong>{result.stop_reason.replaceAll("_", " ")}</strong> · {result.probes_used}/{result.probe_limit} probes used</p>
    <p>{improved ? "A separately validated stable child is retained as the best tested reduction." : "The original finding is retained; no stable reduction has replaced it."}</p>
    <p><Link href={"/findings/" + result.best_finding_id}>{improved ? "View best tested stable child" : "View original finding"}</Link></p>
    <p className="muted">{result.qualification}</p>
    <ol className="minimization-probes">{(result.probes || []).map((probe, index) => <li data-testid="minimization-probe" key={String(probe.mutation_id || index)}>
      <strong>Probe {index + 1} · strength {probe.strength} · {probe.outcome.replaceAll("_", " ")}</strong>
      <p>Screen: {probe.screening_outcome || "Not dispatched"}. Fresh confirmation coverage {(probe.confirmation_outcomes || []).length}/3; {(probe.confirmation_outcomes || []).filter(value => value === "INCORRECT").length} incorrect.</p>
      {probe.outcome === "PASSING" && <p>A simpler passing neighbor is retained as boundary evidence.</p>}
      {probe.mutation_id && <p><Link href={"/mutations/" + probe.mutation_id}>Review probe candidate</Link>{probe.finding_id && <> · <Link href={"/findings/" + probe.finding_id}>Child finding evidence</Link></>}</p>}
    </li>)}</ol>
  </div>;
}

export function MinimizationPanel({ findingId, eligible }: { findingId: string; eligible: boolean }) {
  const [open, setOpen] = useState(false), [jobs, setJobs] = useState<Job[]>([]), [probeLimit, setProbeLimit] = useState(8);
  const [error, setError] = useState(""), [loadError, setLoadError] = useState(""), [busy, setBusy] = useState(""), [loaded, setLoaded] = useState(false);
  const requestKey = useRef<{ body: string; value: string } | null>(null), refresh = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    if (!open) return;
    let active = true, fetching = false;
    async function load() {
      if (!active || fetching) return;
      fetching = true;
      try {
        const list = await allPages<Job>("findings/" + findingId + "/minimizations?limit=100");
        if (active) { setJobs(list.items); setLoaded(true); setLoadError(""); }
      } catch (value) { if (active) setLoadError(actionError(value)); }
      finally { fetching = false; }
    }
    refresh.current = load; void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, findingId]);
  const unfinished = jobs.some(job => !job.finished_at && ["QUEUED", "RUNNING", "REVIEW_REQUIRED"].includes(job.state));
  const validLimit = Number.isInteger(probeLimit) && probeLimit >= 1 && probeLimit <= 8;
  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || unfinished || !eligible || !validLimit) return;
    const body: Request = { probe_limit: probeLimit }, canonical = JSON.stringify(body);
    if (!requestKey.current || requestKey.current.body !== canonical) requestKey.current = { body: canonical, value: crypto.randomUUID() };
    setBusy("start"); setError("");
    try {
      const job = await api<Job>("findings/" + findingId + "/minimize", { ...jsonPost(body), headers: { "Content-Type": "application/json", "Idempotency-Key": requestKey.current.value } });
      setJobs(previous => [job, ...previous.filter(item => item.job_id !== job.job_id)]);
      requestKey.current = null; await refresh.current();
    } catch (value) { setError(actionError(value)); }
    finally { setBusy(""); }
  }
  async function control(jobId: string, action: "resume" | "cancel") {
    if (busy || action === "resume" && !eligible) return;
    setBusy(jobId); setError("");
    try {
      const job = await api<Job>("jobs/" + jobId + "/" + action, { method: "POST" });
      setJobs(previous => previous.map(item => item.job_id === job.job_id ? job : item));
      await refresh.current();
    } catch (value) { setError(actionError(value)); }
    finally { setBusy(""); }
  }
  return <section className="form-panel minimization-panel"><h2>Bounded finding refinement</h2>
    <p>Refinement keeps the original finding and tests simpler children. A reduced failure needs independent validation, a separate screen and three fresh incorrect confirmations.</p>
    <details onToggle={event => setOpen(event.currentTarget.open)}><summary>Find a simpler validated failure</summary>
      {open && <><p className="muted">The original run’s remaining candidate, target-call, dollar and wall-clock ceilings apply. Full experiments and expired runs can stop without a probe. Each new image pauses for human review before target testing.</p>
        {!eligible && <p className="review-warning">Current stable, validated source evidence is required to start or resume refinement.</p>}
        <p>Brightness moves toward its unchanged point, strength 0.5. Deterministic clutter removes retained layers; unsupported generated backgrounds are reported explicitly.</p>
        <form onSubmit={start}><label>Maximum probes<input aria-label="Maximum minimization probes" type="number" min={1} max={8} step={1} value={probeLimit} onChange={event => setProbeLimit(Number(event.target.value))} disabled={!!busy} required /></label><button className="secondary" data-testid="start-minimization" disabled={!!busy || unfinished || !eligible || !validLimit}>{busy === "start" ? "Requesting…" : "Start bounded refinement"}</button></form>
        {error && <p className="error" role="alert">{error} An unchanged retry preserves the request identity.</p>}
        {loadError && <p className="error" role="alert">{loadError}</p>}
        {!loaded && <p role="status">Loading retained refinement jobs…</p>}
        {loaded && !jobs.length && <p>No refinement job has been recorded.</p>}
        {jobs.map(job => { const waiting = job.state === "REVIEW_REQUIRED" && !job.finished_at; return <article className="minimization-job" data-testid="minimization-job" data-job-id={job.job_id} key={job.job_id}>
          <h3>Refinement {job.job_id.slice(0, 8)} · {job.state}</h3>
          {job.result?.synthetic && <p className="review-warning">LOCAL_FIXTURE · simulated evidence</p>}
          {job.error_code && <p>Code: {job.error_code}</p>}
          {job.result?.minimization ? <ReductionResult result={job.result.minimization} /> : <p role="status">Waiting for retained probe evidence…</p>}
          {waiting && <div className="actions"><p>Open the latest probe candidate and record an independent review. Resuming uses that reviewed result; only VALID images can be sent to the target.</p><button className="primary" data-testid="resume-minimization" disabled={!!busy || !eligible} onClick={() => control(job.job_id, "resume")}>Resume after candidate review</button><button className="quiet" disabled={!!busy} onClick={() => control(job.job_id, "cancel")}>Stop this refinement</button></div>}
          <JobCosts usage={job.usage} />
        </article>; })}
      </>}
    </details>
  </section>;
}
