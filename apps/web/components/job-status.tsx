"use client";
import { useEffect, useState } from "react";
import { api, type components } from "@/lib/client-api";
export type Job = components["schemas"]["JobRef"];
export function JobStatus({ initial, onComplete }: { initial: Job; onComplete?: (job: Job) => void }) {
  const [job, setJob] = useState(initial), [error, setError] = useState(""), [pollAttempt, setPollAttempt] = useState(0);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const value = await api<Job>("jobs/" + initial.job_id);
        if (!active) return;
        setJob(value);
        if (["QUEUED", "RUNNING"].includes(value.state)) timer = setTimeout(poll, 800);
        else onComplete?.(value);
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "Job status is unavailable."); }
    }
    timer = setTimeout(poll, 600);
    return () => { active = false; clearTimeout(timer); };
  }, [initial.job_id, onComplete, pollAttempt]);
  const result = job.result, usage = job.usage;
  return <div className="job-status" role="status" aria-live="polite"><strong>{job.state === "SUCCEEDED" ? "Check completed" : job.state === "REVIEW_REQUIRED" ? "Review required" : job.state === "FAILED" ? "Check failed" : job.state === "RUNNING" ? "Check running…" : "Waiting for worker…"}</strong>
    {result?.synthetic && <span className="badge">Synthetic fixture</span>}
    {result?.parsed_count != null && <p>Target returned {result.parsed_count}. {result.target_status === "CORRECT" ? "Matches the human count." : result.target_status === "INCORRECT" ? "Differs from the human count." : "Interpretation needs review."}</p>}
    {result?.detected_count != null && <p>Segmentation proposed {result.detected_count} masks. Human approval is required.</p>}
    {job.error_code && <p>Code: {job.error_code}. Check the provider gate, allowance and worker before retrying.</p>}
    {usage && <p className="muted">Actual ${Number(usage.actual_usd || 0).toFixed(6)} · Estimated ${Number(usage.estimated_usd || 0).toFixed(6)} · Unresolved ${Number(usage.unresolved_usd || 0).toFixed(6)}</p>}
    {error && <div role="alert" className="error"><p>{error}</p><button type="button" className="secondary" onClick={() => { setError(""); setPollAttempt(value => value + 1); }}>Retry status check</button></div>}
  </div>;
}
