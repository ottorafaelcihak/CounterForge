"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, allPages, jsonPost, ApiError, type components } from "@/lib/client-api";

type Run = components["schemas"]["ExperimentView"];
type Candidate = components["schemas"]["Mutation"];
type Finding = components["schemas"]["Finding"];
type EventRecord = components["schemas"]["StoredExperimentEvent"];
const terminal = new Set(["COMPLETED", "CANCELLED", "BUDGET_EXHAUSTED", "FAILED", "SEED_INVALID_FOR_SEARCH"]);
const active = new Set(["BASELINE_VALIDATING", "READY", "PLANNING", "MUTATING", "VALIDATING", "EVALUATING", "CONFIRMING"]);
const dollars = (value: number) => "$" + value.toFixed(6);
const reasons: Record<string, string> = {
  CANDIDATE_HUMAN_REVIEW_REQUIRED: "Review candidates to continue.",
  BASELINE_REVIEW_REQUIRED: "The seed baseline needs review; search has not been approved.",
  SCREENING_REVIEW_REQUIRED: "The screening result is unresolved. It is not a counting failure.",
  CONFIRMATION_REVIEW_REQUIRED: "Confirmation is incomplete or unresolved. It cannot establish a stable failure.",
  OWNER_REQUEST: "Stopped at the owner's request.", RUNTIME_EXPIRED: "The run's wall-clock limit has expired.",
  BOUNDED_PLAN_COMPLETE: "The bounded plan is complete.",
  CONFIRMATION_TARGET_CALL_CAP: "The remaining call allowance cannot cover all fresh confirmations.",
  CONFIRMATION_DOLLAR_CAP: "The remaining dollar allowance cannot cover all fresh confirmations.",
  CONFIRMATION_RUNTIME_CAP: "The remaining runtime cannot cover all fresh confirmations.",
  RECOVERY_PROVIDER_OUTCOME_REVIEW_REQUIRED: "A recovered provider operation needs review before any more requests.",
};

export function ExperimentCockpit({ experimentId }: { experimentId: string }) {
  const [run, setRun] = useState<Run | null>(null), [candidates, setCandidates] = useState<Candidate[]>([]), [findings, setFindings] = useState<Finding[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]), [streamStatus, setStreamStatus] = useState("Connecting to saved events…");
  const [error, setError] = useState(""), [loadError, setLoadError] = useState(""), [busy, setBusy] = useState("");
  const reload = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    let mounted = true, fetching = false, again = false, wire: EventSource | null = null;
    let lastId = 0, finished = false;
    function appendEvents(records: EventRecord[]) {
      const fresh = records.filter(record => Number.isSafeInteger(record.id) && record.id > lastId && record.experiment_id === experimentId && typeof record.event === "string").sort((a, b) => a.id - b.id);
      if (!fresh.length || !mounted) return;
      lastId = fresh[fresh.length - 1].id;
      setEvents(old => [...new Map([...old, ...fresh].map(record => [record.id, record])).values()].slice(-200));
    }
    const path = "experiments/" + experimentId;
    function connect() {
      if (!mounted || wire || finished) return;
      wire = new EventSource("/api/backend/" + path + "/events");
      wire.onopen = () => { if (mounted) { setStreamStatus("Live saved events"); void refresh(); } };
      wire.onmessage = message => {
        try {
          const record = JSON.parse(message.data) as EventRecord;
          appendEvents([record]);
          void refresh();
        } catch { if (mounted) setStreamStatus("An event could not be read; the snapshot remains authoritative."); }
      };
      wire.onerror = () => {
        if (!mounted || finished) return;
        setStreamStatus("Reconnecting; refreshing the saved snapshot.");
        if (wire?.readyState === EventSource.CLOSED) { wire.close(); wire = null; }
        void refresh();
      };
    }
    async function refresh() {
      if (!mounted) return;
      if (fetching) { again = true; return; }
      fetching = true;
      try {
        const [snapshot, mutations, results] = await Promise.all([
          api<Run>(path), allPages<Candidate>(path + "/mutations?limit=100"), allPages<Finding>(path + "/findings?limit=100"),
        ]);
        if (mounted) {
          setRun(snapshot); setCandidates(mutations.items); setFindings(results.items); setLoadError("");
          finished = terminal.has(snapshot.state);
          if (finished) {
            wire?.close(); wire = null;
            setStreamStatus("Saved events; experiment finished.");
            for (let page = 0; page < 10 && mounted; page++) {
              const history = await api<EventRecord[]>(path + "/event-history?limit=100&after=" + lastId);
              appendEvents(history);
              if (history.length < 100) break;
            }
          } else connect();
        }
      } catch (e) {
        if (mounted) {
          setLoadError(e instanceof Error ? e.message : "The saved experiment could not be loaded.");
          if (e instanceof ApiError && e.status === 401) { wire?.close(); wire = null; setStreamStatus("Sign in again to reconnect."); }
        }
      } finally {
        fetching = false;
        if (again && mounted) { again = false; void refresh(); }
      }
    }
    reload.current = refresh;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    const focus = () => void refresh(); window.addEventListener("focus", focus);
    return () => { mounted = false; wire?.close(); window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [experimentId]);

  async function control(action: "pause" | "resume" | "cancel") {
    if (busy || !run) return;
    if (action === "cancel" && !window.confirm("Cancel this experiment? In-flight requests may finish and remain charged.")) return;
    setBusy(action); setError("");
    try { await api("experiments/" + experimentId + "/" + action, jsonPost({})); await reload.current(); }
    catch (e) { setError(e instanceof Error ? e.message : "The control request could not be confirmed. Reload the saved state."); }
    finally { setBusy(""); }
  }
  async function approveDemo(id: string) {
    if (busy) return;
    setBusy(id); setError("");
    try { await api("findings/" + id + "/demo-approve", jsonPost({})); await reload.current(); }
    catch (e) { setError(e instanceof Error ? e.message : "Demo approval could not be recorded."); }
    finally { setBusy(""); }
  }
  if (!run) return <main className="workspace"><h1>Experiment cockpit</h1>{loadError ? <p className="error" role="alert">{loadError}</p> : <p role="status">Loading the saved experiment…</p>}<button className="secondary" onClick={() => reload.current()}>Reload experiment</button></main>;
  const metrics = run.metrics;
  const reviewCount = candidates.filter(c => !c.validation || c.validation.verdict === "REVIEW_REQUIRED").length;
  return <main className="workspace cockpit"><nav className="evidence-navigation"><Link href={"/projects/" + run.project_id}>Project setup</Link><button className="quiet" onClick={() => reload.current()}>Reload experiment</button></nav>
    <div className="page-heading"><div><h1>Experiment cockpit</h1><p>Requested strategy: {run.mode === "RANDOM_BASELINE" ? "seeded random baseline" : "initial Spark plan"} · {run.id.slice(0, 8)}</p></div><span className="badge verdict" data-testid="experiment-state">{run.state}</span></div>
    {run.synthetic && <p className="review-warning">Synthetic provider fixture run. These results do not establish a real model weakness or billed API usage.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {loadError && <p className="error" role="alert">{loadError}</p>}
    {run.stop_reason && <div className={run.state === "COMPLETED" ? "success" : "review-warning"}><p>{reasons[run.stop_reason] || run.stop_reason.replaceAll("_", " ")}</p><small>Saved reason: {run.stop_reason}</small></div>}
    <section className="run-controls" aria-label="Experiment controls"><button className="secondary" onClick={() => control("pause")} disabled={!!busy || !active.has(run.state)}>Pause</button><button className="primary" onClick={() => control("resume")} disabled={!!busy || run.state !== "PAUSED" || !!run.resume_blocked_reason}>Resume</button><button className="quiet danger" onClick={() => control("cancel")} disabled={!!busy || terminal.has(run.state) || run.state === "CANCEL_REQUESTED"}>Cancel experiment</button><span className="muted" role="status">{streamStatus}</span></section>
    {run.resume_blocked_reason && <p className="review-warning" role="status">{run.resume_blocked_reason}</p>}
    <section className="baseline-strip" aria-label="Seed baseline"><strong data-testid="baseline-progress">Seed baseline: {run.baseline_completed} / 3</strong><span>All three replies must be interpretable and correct before search.</span></section>
    <section className="run-metrics" aria-label="Run limits and usage">
      <article><span>Committed cap exposure</span><strong data-testid="committed-exposure">{dollars(run.spent_usd + run.reserved_usd)}</strong><small data-testid="remaining-headroom">{dollars(Math.max(0, run.limits.max_estimated_usd - run.spent_usd - run.reserved_usd))} remaining. Includes actual usage, settled estimates, unresolved charges and reservations.</small></article>
      <article><span>Target calls consumed</span><strong data-testid="target-call-count">{metrics.total_target_calls} / {run.limits.max_target_calls}</strong><small>{run.reserved_target_calls} reserved</small></article>
      <article><span>Canonical candidates</span><strong>{metrics.generated_candidates} / {run.limits.max_candidates}</strong><small>{metrics.valid_candidates} valid · {metrics.invalid_candidates} invalid · {metrics.review_required_candidates} need review</small></article>
      <article><span>Actual provider usage (included)</span><strong>{dollars(metrics.actual_provider_usage_usd)}</strong><small>Accounted spend: {dollars(run.spent_usd)}. Actual cost uses retained provider usage and prices.</small></article>
      <article><span>Reserved / conservative estimate</span><strong>{dollars(run.reserved_usd)}</strong><small>Settled estimates: {dollars(Math.max(0, metrics.conservative_estimate_usd - run.reserved_usd))}</small></article>
      <article><span>Unresolved charges (included)</span><strong>{dollars(metrics.unresolved_charges_usd)}</strong><small>Remain counted until operator reconciliation</small></article>
      <article><span>Maximum dollars / wall clock</span><strong>{dollars(run.limits.max_estimated_usd)}</strong><small>{Math.floor(run.elapsed_seconds)} / {run.limits.max_runtime_seconds} seconds; paused time counts</small></article>
    </section>
    <section><div className="section-toolbar"><h2>Candidates</h2><span>{reviewCount} awaiting review</span></div>{candidates.length ? <div className="candidate-run-list">{candidates.map(candidate => <article className="form-panel" key={candidate.id}><div className="history-heading"><h3>{candidate.family.replaceAll("_", " ")}</h3><span className={"badge " + (candidate.validation?.verdict === "VALID" ? "verdict-valid" : candidate.validation?.verdict === "INVALID" ? "verdict-invalid" : "verdict-review")}>{candidate.validation?.verdict || "REVIEW_REQUIRED"}</span></div><p>Strength {candidate.strength} · {candidate.state.replaceAll("_", " ")}</p><p className="muted">{candidate.validation?.confidence_note || "A sealed validation report is required."}</p>{!!candidate.validation?.reason_codes?.length && <p>Reasons: {candidate.validation.reason_codes.join(", ")}</p>}{candidate.synthetic && <p className="badge">Synthetic provider fixture</p>}<p><Link href={"/mutations/" + candidate.id}>Review candidate</Link></p></article>)}</div> : <p className="empty-state">No candidate has been generated. Baseline and planning outcomes remain in the saved timeline.</p>}</section>
    <section><h2>Findings and fresh confirmations</h2><p className="muted">Screening is separate. A final stable classification requires three fresh incorrect confirmations and current VALID evidence.</p>{findings.length ? findings.map(finding => <article className="form-panel" key={finding.id}><div className="history-heading"><h3>{finding.status.replaceAll("_", " ")}</h3><span className="badge">{finding.confirmation.incorrect} / {finding.confirmation.attempts} confirmations incorrect</span></div><p>{finding.qualification}</p>{finding.mutation_id && <p><Link href={"/mutations/" + finding.mutation_id}>Review finding evidence</Link></p>}<p>Human count {finding.ground_truth.value} · Current validity {finding.validation.verdict}</p>{finding.demo_approved ? <p className="success">Demo approved by {finding.demo_approved_by} at {finding.demo_approved_at && new Date(finding.demo_approved_at).toLocaleString()}.</p> : <button className="secondary" disabled={!!busy || finding.status !== "STABLE" || finding.validation.verdict !== "VALID"} onClick={() => approveDemo(finding.id)}>Record human demo approval</button>}</article>) : <p>No finding has been established.</p>}</section>
    <section><h2>Saved event timeline</h2><p className="muted">Most recent 200 events. Reconnects refresh the authoritative snapshot.</p><ol className="run-timeline">{[...events].reverse().map(event => <li key={event.id} data-testid="experiment-event" data-event-id={event.id}><strong>{event.event.replaceAll("_", " ")}</strong><span>Event {event.id}</span><pre>{JSON.stringify(event.payload, null, 2)}</pre></li>)}</ol></section>
  </main>;
}
