"use client";

/* eslint-disable @next/next/no-img-element -- Inspect authenticated, retained image bytes without an optimizer. */

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { api, allPages, assetURL, jsonPost, ApiError, type components } from "@/lib/client-api";
import { confirmDiscard, useUnsaved } from "@/lib/unsaved";

type Candidate = components["schemas"]["Mutation"];
type Revision = components["schemas"]["MutationValidationRevision"];
type ReviewRequest = components["schemas"]["MutationReviewRequest"];
type AuditCode = NonNullable<ReviewRequest["ground_truth_audit"]>["resolved_check_codes"][number];
const imageRoles = [
  ["original", "Original"], ["candidate", "Candidate"], ["background", "Background"],
  ["protected_union", "Protected union"], ["diff", "Pixel difference"],
] as const;

async function readEvidence(id: string) {
  const [candidate, history] = await Promise.all([
    api<Candidate>("mutations/" + id),
    allPages<Revision>("mutations/" + id + "/history?limit=100"),
  ]);
  return { candidate, history: history.items };
}

function verdictClass(verdict: string) {
  return verdict === "VALID" ? "verdict-valid" : verdict === "INVALID" ? "verdict-invalid" : "verdict-review";
}

export function MutationEvidence({ mutationId }: { mutationId: string }) {
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [refinementSource, setRefinementSource] = useState<{ jobId: string; findingId: string } | null>(null);
  const [history, setHistory] = useState<Revision[]>([]);
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false), [stale, setStale] = useState(false);
  const [semantic, setSemantic] = useState<ReviewRequest["semantic"] | "">("");
  const [visibility, setVisibility] = useState<ReviewRequest["visibility"] | "">("");
  const [notes, setNotes] = useState(""), [auditNote, setAuditNote] = useState("");
  const [auditing, setAuditing] = useState(false), [resolved, setResolved] = useState<AuditCode[]>([]);
  const [loaded, setLoaded] = useState<Set<string>>(() => new Set());
  const [failed, setFailed] = useState<string[]>([]), [imageEpoch, setImageEpoch] = useState(0);
  const { dirty, setDirty } = useUnsaved();
  const hasDraft = Boolean(semantic || visibility || notes || auditNote || auditing);
  useEffect(() => { setDirty(hasDraft); }, [hasDraft, setDirty]);
  useEffect(() => () => setDirty(false), [setDirty]);
  useEffect(() => {
    let active = true;
    readEvidence(mutationId).then(data => {
      if (active) { setCandidate(data.candidate); setHistory(data.history); }
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Evidence could not be loaded."); });
    return () => { active = false; };
  }, [mutationId]);

  useEffect(() => {
    const jobId = candidate?.minimization_job_id;
    if (!jobId) return;
    let active = true;
    void api<components["schemas"]["JobRef"]>("jobs/" + jobId).then(job => {
      if (active) { const findingId = job.result?.minimization?.source_finding_id; setRefinementSource(findingId ? { jobId, findingId } : null); }
    }).catch(() => {});
    return () => { active = false; };
  }, [candidate?.minimization_job_id]);

  function resetDraft() {
    setSemantic(""); setVisibility(""); setNotes(""); setAuditNote(""); setAuditing(false); setResolved([]);
  }
  async function reload() {
    if (busy || !confirmDiscard(dirty)) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await readEvidence(mutationId);
      setCandidate(data.candidate); setHistory(data.history); resetDraft(); setStale(false);
      setLoaded(new Set()); setFailed([]); setImageEpoch(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Evidence could not be loaded."); }
    finally { setBusy(false); }
  }

  const report = candidate?.validation;
  const assets = candidate?.assets || {};
  const checks = report?.checks || [];
  const structuralFailure = checks.some(c => c.mandatory !== false && c.status === "FAIL" && !c.code.startsWith("HUMAN_"));
  const failedHuman = checks.filter((c): c is typeof c & { code: AuditCode } => c.mandatory !== false && c.status === "FAIL" && (c.code === "HUMAN_SEMANTICS" || c.code === "HUMAN_VISIBILITY"));
  const needsAudit = report?.verdict === "INVALID" && semantic !== "additional_object" && semantic !== "removed_object";
  const imagesReady = imageRoles.every(([role]) => assets[role] && loaded.has(assets[role]));
  const canReview = Boolean(report?.report_sha256 && !structuralFailure && imagesReady && !failed.length && !stale);
  const canSubmit = canReview && !busy && Boolean(semantic && visibility && notes.trim().length >= 10) &&
    (!needsAudit || (auditing && auditNote.trim().length >= 10 && failedHuman.every(c => resolved.includes(c.code))));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || !report?.report_sha256 || !semantic || !visibility || busy) return;
    setBusy(true); setError(""); setMessage("");
    const body: ReviewRequest = {
      prior_report_sha256: report.report_sha256, evidence_hashes: report.evidence_hashes || {},
      semantic, visibility, notes,
      ...(needsAudit ? { ground_truth_audit: { resolved_check_codes: resolved, note: auditNote } } : {}),
    };
    try {
      const next = await api<Candidate>("mutations/" + mutationId + "/review", jsonPost(body));
      setCandidate(next); resetDraft(); setMessage("Review recorded against this exact evidence.");
      try {
        const result = await allPages<Revision>("mutations/" + mutationId + "/history?limit=100");
        setHistory(result.items);
      } catch {
        setError("Your review was recorded. History could not be refreshed; reload the evidence to see it.");
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setStale(true);
      setError(e instanceof Error ? e.message : "Review could not be recorded.");
    } finally { setBusy(false); }
  }

  if (!candidate) return <main className="workspace"><h1>Candidate evidence</h1>
    {error ? <><p className="error" role="alert">{error}</p><button className="secondary" onClick={reload} disabled={busy}>Reload evidence</button></> :
      <p role="status">Loading retained evidence…</p>}</main>;

  const masks = (candidate.mask_snapshot || []).filter(item => typeof item.asset_id === "string");
  const samMasks = Object.entries(assets).filter(([role]) => role.startsWith("sam_mask:"));
  return <main className="workspace mutation-workspace">
    <nav className="evidence-navigation"><Link href="/">Projects</Link>
      <Link href={"/experiments/" + candidate.experiment_id}>Experiment</Link>
      {candidate.parent_mutation_id && <Link href={"/mutations/" + candidate.parent_mutation_id}>Parent candidate</Link>}
      {candidate.minimization_job_id && refinementSource?.jobId === candidate.minimization_job_id && <Link href={"/findings/" + refinementSource.findingId}>Return to finding refinement</Link>}
      {candidate.source_seed_id && <Link href={"/seeds/" + candidate.source_seed_id + "/masks"}>Source mask review</Link>}
      <button className="quiet" onClick={reload} disabled={busy}>Reload evidence</button></nav>
    <div className="page-heading"><div><h1>Candidate evidence</h1><p>{candidate.family.replaceAll("_", " ")} · Strength {candidate.strength} · Revision {candidate.validation_revision ?? "unavailable"}</p></div>
      <span className={"badge verdict " + verdictClass(report?.verdict || "REVIEW_REQUIRED")} data-testid="candidate-verdict">{report?.verdict || "REVIEW_REQUIRED"}</span></div>
    <section className="evidence-summary">
      <div className="count-plaque"><span>Human-established count</span><strong data-testid="human-count">{candidate.human_expected_count ?? "Unknown"}</strong><span>{candidate.protected_label || "Protected instances"}</span></div>
      <p>Review the retained images and checks before recording a decision. The expected count stays fixed.</p>
      <span className="badge">{candidate.synthetic ? "Synthetic provider fixture" : candidate.recipe?.generator === "deterministic" ? "Deterministic transform" : "Retained evidence"}</span>
    </section>
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    {stale && <p className="review-warning">Newer evidence is available. Reload it and review again before submitting.</p>}
    {!!failed.length && <p className="error" role="alert">Some evidence images could not be loaded: {failed.join(", ")}. Reload the evidence before reviewing.</p>}

    <section aria-label="Retained comparison images" className="evidence-gallery">
      {imageRoles.map(([role, label]) => <figure className="evidence-image" data-testid="evidence-image" key={role}>
        {assets[role] ? <img key={assets[role] + ":" + imageEpoch} src={assetURL(assets[role])} alt={label + " for the protected-object count"} loading="eager"
          onLoad={() => setLoaded(old => old.has(assets[role]) ? old : new Set([...old, assets[role]]))}
          onError={() => setFailed(old => old.includes(label) ? old : [...old, label])} /> : <p>Image unavailable</p>}
        <figcaption><strong>{label}</strong>{assets[role] && <a href={assetURL(assets[role])} target="_blank" rel="noreferrer">Open full image</a>}</figcaption>
      </figure>)}
    </section>

    <section className="form-panel"><h2>Independent verification</h2>
      {report ? <><p className="muted">Verifier {report.verifier_version} · Policy {report.policy_version}</p><p>{report.confidence_note}</p>
        <ul className="evidence-checks">{checks.map(check => <li key={check.code}>
          <div><code>{check.code}</code><span className={"check-status " + (check.status === "FAIL" ? "verdict-invalid" : check.status === "PASS" ? "verdict-valid" : "verdict-review")}>{check.status}</span>
            <small>{check.mandatory === false ? "Supporting evidence" : "Required check"}</small></div>
          <div><p>{check.detail}</p>{check.measurements && Object.keys(check.measurements).length > 0 &&
            <dl className="check-measurements">{Object.entries(check.measurements).map(([name, value]) => <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}</dl>}</div>
        </li>)}</ul></> : <p className="review-warning">A current sealed verifier report is required before this candidate can be reviewed or tested.</p>}
    </section>

    <details className="form-panel"><summary>Approved source masks ({masks.length})</summary>
      <div className="mask-evidence-grid">{masks.map((mask, index) => <figure key={String(mask.asset_id)}>
        <img src={assetURL(String(mask.asset_id))} alt={"Approved source mask " + (index + 1)} loading="lazy" />
        <figcaption>Instance {index + 1} · Revision {String(mask.revision)}<br />Reviewer {String(mask.reviewer)}</figcaption>
      </figure>)}</div>
    </details>
    {!!samMasks.length && <details className="form-panel"><summary>Supporting candidate segmentation ({samMasks.length} masks)</summary><p>Segmentation supports inspection. Its count alone does not establish ground truth.</p>
      <div className="mask-evidence-grid">{samMasks.map(([role, id]) => <figure key={role}><img src={assetURL(id)} alt={"Supporting segmentation " + role} loading="lazy" /><figcaption>{role}</figcaption></figure>)}</div>
    </details>}

    <section className="form-panel mutation-review"><h2>Record a human review</h2>
      {structuralFailure ? <p className="error">A required structural check failed. This candidate cannot be approved; prepare a new candidate with valid source evidence.</p> :
        !report?.report_sha256 ? <p>Current verifier evidence is unavailable. Review is blocked.</p> :
        <form onSubmit={submit}>
          <p>Confirm count, added or missing instances, contour seams, clipping and visibility. Automatic semantic and visibility thresholds have not been calibrated.</p>
          {!imagesReady && <p className="muted" role="status">Waiting for the comparison images to load…</p>}
          <label><span id="semantic-integrity-label">Semantic integrity</span><select aria-labelledby="semantic-integrity-label" required value={semantic} onChange={e => setSemantic(e.target.value as ReviewRequest["semantic"] | "")} disabled={busy}>
            <option value="">Choose a decision</option><option value="consistent">Exactly {candidate.human_expected_count} protected instances; no extras or missing objects</option>
            <option value="additional_object">An additional target-class object is present</option><option value="removed_object">A protected object is missing</option><option value="ambiguous">Count or semantics are uncertain</option>
          </select></label>
          <label><span id="visibility-label">Visibility</span><select aria-labelledby="visibility-label" required value={visibility} onChange={e => setVisibility(e.target.value as ReviewRequest["visibility"] | "")} disabled={busy}>
            <option value="">Choose a decision</option><option value="clear">All protected objects are clearly visible</option><option value="ambiguous">Visibility is ambiguous</option><option value="destroyed">Confirmed loss of protected-object visibility</option>
          </select></label>
          <label><span id="review-notes-label">Review notes</span><textarea aria-labelledby="review-notes-label" required minLength={10} maxLength={2000} rows={3} value={notes} onChange={e => setNotes(e.target.value)} disabled={busy} /></label>
          {needsAudit && semantic && failedHuman.length > 0 && <fieldset className="ground-truth-audit"><legend>Resolve a previous human rejection</legend>
            <p>An explicit audit is required to resolve failed human checks. The original rejection stays in the history.</p>
            <label className="checkbox"><input type="checkbox" checked={auditing} onChange={e => setAuditing(e.target.checked)} disabled={busy} />Perform a ground-truth audit</label>
            {auditing && <>{failedHuman.map(check => <label className="checkbox" key={check.code}><input type="checkbox" checked={resolved.includes(check.code)} disabled={busy}
              onChange={e => { const checked = e.target.checked; setResolved(old => checked ? [...new Set([...old, check.code])] : old.filter(code => code !== check.code)); }} />{"Resolve " + check.code}</label>)}
              <label><span id="audit-notes-label">Ground-truth audit notes</span><textarea aria-labelledby="audit-notes-label" minLength={10} maxLength={2000} rows={3} value={auditNote} onChange={e => setAuditNote(e.target.value)} disabled={busy} /></label></>}
          </fieldset>}
          <button className="primary" type="submit" disabled={!canSubmit}>{busy ? "Recording…" : "Record review"}</button>
        </form>}
    </section>

    <section className="history-section"><h2>Validation history</h2>
      <p className="muted">Each revision preserves its report and previous decision.</p>
      <ol className="validation-history">{[...history].sort((a, b) => b.revision - a.revision).map(item => <li className="form-panel" data-testid="validation-revision" key={item.id}>
        <div className="history-heading"><h3>Revision {item.revision}</h3><span className={"badge " + verdictClass(item.report?.verdict || "REVIEW_REQUIRED")}>{item.report?.verdict || "Unsealed legacy evidence"}</span></div>
        <p className="muted">{new Date(item.created_at).toLocaleString()}</p>
        {item.report?.review_audit ? <><p><strong>{item.report.review_audit.actor}</strong> · Previous verdict {item.report.review_audit.previous_verdict}</p><p>{item.report.review_audit.note}</p>
          {item.report.review_audit.ground_truth_audit_sha256 && <p>Ground-truth audit recorded</p>}</> : <p>{item.report ? "Automatic verification or supporting evidence refresh." : "No current sealed report is available."}</p>}
        {item.report_asset_id && <a href={assetURL(item.report_asset_id)} target="_blank" rel="noreferrer">Open retained report</a>}
        {item.ground_truth_audit_asset_id && <p><a href={assetURL(item.ground_truth_audit_asset_id)} target="_blank" rel="noreferrer">Open retained ground-truth audit</a></p>}
      </li>)}</ol>
    </section>
    <details className="form-panel evidence-hashes"><summary>Recipe and evidence hashes</summary>
      <dl>{Object.entries(assets).map(([role, id]) => <div key={role}><dt>{role}</dt><dd><a href={assetURL(id)} target="_blank" rel="noreferrer">Retained asset</a><code className="hash">{candidate.asset_hashes?.[role] || "Hash unavailable"}</code></dd></div>)}</dl>
      <pre>{JSON.stringify(candidate.recipe, null, 2)}</pre>
    </details>
  </main>;
}
