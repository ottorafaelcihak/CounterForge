"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type components } from "@/lib/client-api";
type Run = components["schemas"]["ExperimentView"];
export function RunSummary({ run }: { run: Run }) {
  const metrics = run.metrics;
  return <article className="form-panel run-summary" data-testid="run-summary"><h3><Link href={"/experiments/" + run.id}>{run.state}</Link></h3>
    <p>{run.id.slice(0, 8)} · {run.started_at ? new Date(run.started_at).toLocaleString() : "Start time unavailable"} · {run.mode}</p>
    {run.synthetic && <p className="review-warning">LOCAL_FIXTURE · simulated evidence</p>}
    <dl className="metrics-grid"><div><dt>Generated</dt><dd>{metrics.generated_candidates}</dd></div><div><dt>Valid</dt><dd>{metrics.valid_candidates}</dd></div><div><dt>Rejected</dt><dd>{metrics.invalid_candidates}</dd></div><div><dt>Review required</dt><dd>{metrics.review_required_candidates}</dd></div><div><dt>Stable findings</dt><dd>{metrics.stable_findings}</dd></div><div><dt>Target calls</dt><dd>{metrics.total_target_calls} / {run.limits.max_target_calls}</dd></div></dl>
    <p>Baseline {run.baseline_completed} / 3 · screening calls {metrics.valid_screening_calls}. Three correct baseline replies are required before search.</p>
    <p>Actual ${metrics.actual_provider_usage_usd.toFixed(6)} · conservative estimates/reserved ${metrics.conservative_estimate_usd.toFixed(6)} · unresolved ${metrics.unresolved_charges_usd.toFixed(6)}. Reserved exposure ${run.reserved_usd.toFixed(6)} is included, not added twice.</p>
    {run.stop_reason && <p>Saved stop reason: {run.stop_reason}</p>}
    <StoredMetricDetails metrics={metrics} />
    <Link href={"/experiments/" + run.id + "/mutations"}>Inspect all candidate attempts</Link>
  </article>;
}
export function ProjectRuns({ projectId }: { projectId: string }) {
  const [runs, setRuns] = useState<Run[]>([]), [error, setError] = useState(""), [loaded, setLoaded] = useState(false), [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    // Server returns newest first; one short page is the latest five.
    api<{ items: Run[] }>("projects/" + projectId + "/experiments?limit=5", { signal: controller.signal }).then(data => { if (!controller.signal.aborted) { setRuns(data.items); setLoaded(true); setError(""); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [projectId, revision]);
  const latest = runs;
  return <section aria-label="Latest project runs"><div className="section-toolbar"><h2>Latest runs</h2><button className="quiet" onClick={() => setRevision(value => value + 1)}>Reload latest runs</button></div>
    <p className="muted">Stored API counts and spend for each run. Showing the latest five runs; gallery filters do not change these values.</p>
    {error && <p className="error" role="alert">{error}</p>}{!loaded && !error && <p role="status">Loading saved runs…</p>}{loaded && !runs.length && <p className="empty-state">No runs yet. Prepare a reviewed contract and authorized target to start a bounded experiment.</p>}
    {latest.map(run => <RunSummary key={run.id} run={run} />)}
  </section>;
}

export function StoredMetricDetails({ metrics }: { metrics: components["schemas"]["Metrics"] }) {
  return <details className="form-panel"><summary>Stored denominators, spend categories and reports</summary>
    <p>These counts describe recorded outcomes. They do not estimate audited validity precision or universal reproducibility. Screening and confirmation counts exclude seed-baseline and retest calls, which are included in total target calls.</p>
    {([['screening', 'Screening'], ['confirmation', 'Fresh confirmation']] as const).map(([key, label]) => { const counts = metrics[key]; return counts ? <p key={key}>{label}: {counts.total} observations · {counts.interpretable} interpretable · {counts.correct} correct · {counts.incorrect} incorrect · UNKNOWN {counts.unknown} · provider errors {counts.provider_errors}. Incorrect denominator: {counts.interpretable}; uncertainty denominator: {counts.total}. A zero denominator has no rate.</p> : <p key={key}>{label} denominators unavailable from this saved summary.</p>; })}
    <p>Stored finding classifications: {Object.entries(metrics.finding_counts || {}).map(([status, count]) => status + ": " + count).join(" · ") || "No classifications recorded"}.</p>
    {Object.entries(metrics.spend_by_category || {}).map(([category, usage]) => <p key={category}>{category}: actual ${usage.actual_usd.toFixed(6)} · settled estimate ${usage.estimated_usd.toFixed(6)} · unresolved ${usage.unresolved_usd.toFixed(6)} · reserved ${usage.reserved_usd.toFixed(6)}</p>)}
    {!!metrics.report_references?.length && <ul>{metrics.report_references.map(report => <li key={report.mutation_id}><Link href={"/mutations/" + report.mutation_id}>Candidate {report.mutation_id.slice(0, 8)}</Link> · revision {report.revision} · <a href={"/api/backend/assets/" + report.report_asset_id + "/content"} download>Download retained validation report</a></li>)}</ul>}
  </details>;
}
