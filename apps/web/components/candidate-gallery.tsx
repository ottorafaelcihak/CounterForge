"use client";
/* eslint-disable @next/next/no-img-element -- Same-origin retained evidence. */
import Link from "next/link";
import { useEffect, useState } from "react";
import { allPages, assetURL, type components } from "@/lib/client-api";
import { filterCandidates, type CandidateFilter } from "@/lib/candidate-filters";
type Candidate = components["schemas"]["Mutation"];
export function CandidateGallery({ candidates, experimentId, standalone = false }: { candidates: Candidate[]; experimentId: string; standalone?: boolean }) {
  const [verdict, setVerdict] = useState<CandidateFilter>("ALL"), [family, setFamily] = useState(""), [state, setState] = useState("");
  const filtered = filterCandidates(candidates, verdict, family, state);
  return <section aria-label="Candidate rejection explorer"><div className="section-toolbar"><h2>Candidates</h2>{!standalone && <Link href={"/experiments/" + experimentId + "/mutations"}>Open rejection explorer</Link>}</div>
    <p>Every retained attempt is included. UNKNOWN checks and unsealed reports do not establish validity.</p>
    <div className="candidate-filters"><label><span id="candidate-validity-filter">Validity filter</span><select aria-labelledby="candidate-validity-filter" value={verdict} onChange={e => setVerdict(e.target.value as CandidateFilter)}>
      <option value="ALL">All verdicts</option><option value="VALID">VALID</option><option value="INVALID">INVALID · rejected</option><option value="REVIEW_REQUIRED">REVIEW_REQUIRED</option><option value="UNKNOWN">UNKNOWN checks / unsealed</option>
    </select></label><label><span id="candidate-family-filter">Mutation family</span><select aria-labelledby="candidate-family-filter" value={family} onChange={e => setFamily(e.target.value)}><option value="">All families</option>{[...new Set(candidates.map(item => item.family))].map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      <label><span id="candidate-state-filter">Candidate state</span><select aria-labelledby="candidate-state-filter" value={state} onChange={e => setState(e.target.value)}><option value="">All states</option>{[...new Set(candidates.map(item => item.state))].map(value => <option key={value}>{value}</option>)}</select></label></div>
    <p role="status" data-testid="candidate-filter-count">Showing {filtered.length} of {candidates.length} retained candidates. These are gallery counts; run metrics use the stored API summary.</p>
    {!candidates.length ? <p className="empty-state">No candidate has been generated. Baseline and planning outcomes remain in the saved timeline.</p> : !filtered.length ? <p className="empty-state">No retained candidate matches these filters. Choose all verdicts, families and states to see every attempt.</p> :
      <div className="candidate-run-list">{filtered.map(candidate => <article className="form-panel" key={candidate.id} data-testid="candidate-card">
        <div className="history-heading"><h3>{candidate.family.replaceAll("_", " ")}</h3><span className={"badge " + (candidate.validation?.verdict === "VALID" ? "verdict-valid" : candidate.validation?.verdict === "INVALID" ? "verdict-invalid" : "verdict-review")}>{candidate.validation?.verdict || "REVIEW_REQUIRED"}</span></div>
        {standalone && <div className="candidate-thumbnails">{["original", "candidate"].map(role => { const id = candidate.assets?.[role] || (role === "candidate" ? candidate.image_asset_id : null); return id ? <figure key={role}><img src={assetURL(id)} alt={role + " image for " + candidate.family.replaceAll("_", " ")} loading="lazy" /><figcaption>{role}</figcaption></figure> : <p key={role}>{role} image unavailable</p>; })}</div>}
        <p>Strength {candidate.strength} · {candidate.state.replaceAll("_", " ")}</p><p className="muted">{candidate.validation?.confidence_note || "A sealed validation report is required."}</p>
        {candidate.validation && <><p>Policy {candidate.validation.policy_version} · verifier {candidate.validation.verifier_version}</p><ul>{candidate.validation.checks.filter(check => check.status !== "PASS").map(check => <li key={check.code}><strong>{check.code}: {check.status}</strong> · {check.detail}</li>)}</ul>
          {candidate.validation.review_audit && <p>Reviewer {candidate.validation.review_audit.actor} · {candidate.validation.review_audit.semantic} · {candidate.validation.review_audit.visibility}</p>}</>}
        {!!candidate.validation?.reason_codes?.length && <p>Reasons: {candidate.validation.reason_codes.join(", ")}</p>}{candidate.synthetic && <p className="badge">Synthetic provider fixture</p>}
        <p><Link href={"/mutations/" + candidate.id}>Review candidate</Link></p>
      </article>)}</div>}
  </section>;
}
export function CandidateExplorer({ experimentId }: { experimentId: string }) {
  const [items, setItems] = useState<Candidate[]>([]), [error, setError] = useState(""), [loaded, setLoaded] = useState(false), [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    allPages<Candidate>("experiments/" + experimentId + "/mutations?limit=100", { signal: controller.signal }).then(data => { if (!controller.signal.aborted) { setItems(data.items); setLoaded(true); setError(""); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [experimentId, revision]);
  return <main className="workspace"><nav className="evidence-navigation"><Link href="/projects">Projects</Link><Link href={"/experiments/" + experimentId}>Experiment</Link><button className="quiet" onClick={() => setRevision(value => value + 1)}>Reload candidates</button></nav><h1>Candidate rejection explorer</h1>
    {error && <p className="error" role="alert">{error}</p>}{!loaded && !error ? <p role="status">Loading retained attempts…</p> : <CandidateGallery key={experimentId} experimentId={experimentId} candidates={items} standalone />}</main>;
}
