"use client";
/* Authenticated evidence images are deliberately served through our cookie-protected proxy. */
/* eslint-disable @next/next/no-img-element */
import { useUnsaved } from "@/lib/unsaved";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, allPages, assetURL, jsonPost, type components } from "@/lib/client-api";
import { JobStatus, type Job } from "./job-status";
import { MaskCanvas, type MaskCanvasHandle } from "./mask-canvas";
type Seed = components["schemas"]["Seed"];
type Mask = components["schemas"]["Mask"];
type Review = components["schemas"]["MaskReviewState"];
type Revision = components["schemas"]["MaskRevision"];
type Contract = components["schemas"]["Contract"];
type Capabilities = components["schemas"]["SetupCapabilities"];
const blockingText: Record<string, string> = {
  MASK_COUNT_MISMATCH: "Provide one distinct mask for every human-counted object.",
  MASK_REVIEW_REQUIRED: "Inspect and approve each current mask revision.",
  COUNT_MISMATCH_UNACKNOWLEDGED: "Acknowledge the detection discrepancy after correcting the masks.",
  SEGMENTATION_REVIEW_REQUIRED: "The segmentation was incomplete. Correct every instance and acknowledge the result.",
  UNSUPPORTED_COUNT: "This workflow supports one to sixteen protected objects.",
};
export function MaskWorkspace({ seedId }: { seedId: string }) {
  const [seed, setSeed] = useState<Seed | null>(null), [masks, setMasks] = useState<Mask[]>([]), [review, setReview] = useState<Review | null>(null), [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [selectedId, setSelectedId] = useState(""), [overlay, setOverlay] = useState(true);
  const { dirty, setDirty } = useUnsaved();
  const [objectLabel, setObjectLabel] = useState("blue container"), [notes, setNotes] = useState(""), [ackNotes, setAckNotes] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false), [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Revision[] | null>(null), [created, setCreated] = useState<Contract | null>(null);
  const [dollars, setDollars] = useState(5), [calls, setCalls] = useState(3), [runtime, setRuntime] = useState(120);
  const editor = useRef<MaskCanvasHandle>(null);
  const path = "seeds/" + seedId;
  const selected = masks.find(mask => mask.id === selectedId);
  const load = useCallback(async () => {
    try {
      const [s, m, r, c] = await Promise.all([api<Seed>("seeds/" + seedId),
        api<{items: Mask[]}>("seeds/" + seedId + "/masks?limit=100"),
        api<Review>("seeds/" + seedId + "/mask-review"), api<Capabilities>("setup")]);
      const ordered = [...m.items].sort((a, b) => a.object_ordinal - b.object_ordinal);
      setSeed(s); setMasks(ordered); setReview(r); setCapabilities(c);
      setSelectedId(current => ordered.some(mask => mask.id === current) ? current : ordered[0]?.id || "");
      if (m.items[0]) setObjectLabel(m.items[0].object_label);
    } catch (e) { setError(e instanceof Error ? e.message : "Seed review could not be loaded."); }
  }, [seedId]);
  useEffect(() => { let active = true; void Promise.resolve().then(() => { if (active) return load(); }); return () => { active = false; }; }, [load]);
  const completed = useCallback((value: Job) => { setJob(value); void load(); }, [load]);
  async function action(operation: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : "The action could not be completed."); }
    finally { setBusy(false); }
  }
  function choose(id: string) {
    if (dirty && !window.confirm("Discard the unsaved mask correction?")) return;
    setSelectedId(id); setHistory(null); setNotes(""); setDirty(false); setError(""); setNotice("");
  }
  async function save() {
    await action(async () => {
      if (!notes.trim()) throw new Error("Describe the correction before saving.");
      const file = await editor.current?.png(); if (!file) throw new Error("The mask canvas is not ready.");
      const form = new FormData(); form.set("file", file, "mask.png"); form.set("notes", notes);
      if (!selected) form.set("object_label", objectLabel);
      const mask = await api<Mask>(path + (selected ? "/masks/" + selected.id + "/revisions" : "/masks"), {
        method: "POST", body: form, headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      setSelectedId(mask.id); setHistory(null); setDirty(false); setNotes(""); await load();
      setNotice("Mask saved as revision " + mask.revision + ". Review this exact revision before approving.");
    });
  }
  async function approve() {
    if (!selected || dirty) return;
    await action(async () => {
      if (!notes.trim()) throw new Error("Add review notes before approving this revision.");
      await api<Mask>(path + "/masks/" + selected.id + "/approve", jsonPost({ decision: "VALID", reason_code: "HUMAN_REVIEW", notes }));
      await load(); setNotes(""); setHistory(null); setNotice("This mask revision is approved.");
    });
  }
  async function retire() {
    if (!selected || dirty) return;
    await action(async () => {
      if (!notes.trim()) throw new Error("Explain why this instance is extra before retiring it.");
      await api<Mask>(path + "/masks/" + selected.id + "/retire", jsonPost({ decision: "INVALID", reason_code: "EXTRA_INSTANCE", notes }));
      setHistory(null); await load(); setNotice("Extra mask retired. Its history is retained.");
    });
  }
  async function showHistory() {
    if (!selected) return;
    await action(async () => { setHistory((await allPages<Revision>(path + "/masks/" + selected.id + "/revisions?limit=100")).items); });
  }
  async function segment() {
    await action(async () => {
      if (dirty) throw new Error("Save or discard the current correction before requesting segmentation.");
      setJob(await api<Job>(path + "/segment", jsonPost({ concept: objectLabel,
        limits: { max_estimated_usd: dollars, max_calls: calls, max_runtime_seconds: runtime } })));
    });
  }
  async function acknowledge(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await action(async () => {
      if (!review) return;
      setReview(await api<Review>(path + "/masks/acknowledgements", jsonPost({
        detected_count: review.detected_count, human_count: review.expected_count, notes: ackNotes,
      }))); setNotice("Detection discrepancy acknowledged for this exact reviewed mask set."); setAckNotes("");
    });
  }
  async function createContract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await action(async () => {
      if (!seed || !review?.can_create_contract || dirty) return;
      const document: components["schemas"]["VisualTestContract"] = {
        schema_version: "1.0.0", case_id: seed.case_key, task_type: "object_counting", prompt: seed.question,
        expected: seed.expected, protected: { label: objectLabel, expected_instances: seed.expected.value,
          mask_asset_ids: masks.map(mask => mask.mask_asset_id), min_visible_fraction: 0.95 },
        allowed_mutations: ["background_replacement", "background_clutter", "brightness_shift"],
        forbidden_mutations: ["object_addition", "object_removal", "protected_region_repaint"],
        validation_policy: { uncertain: "REVIEW_REQUIRED", require_mask_review: true },
        reproduction: { attempts: 3, required_failures: 3 },
        limits: { max_candidates: 20, max_target_calls: 60, max_estimated_usd: 10, max_runtime_seconds: 1200 },
      };
      const value = await api<Contract>(path + "/contracts", jsonPost(document)); setCreated(value);
      setNotice("Contract version " + value.version + " saved. Expected count remains " + seed.expected.value + ".");
    });
  }
  const discrepancy = review && (review.mismatch || (review.segmentation_outcome != null && review.segmentation_outcome !== "COMPLETED"));
  const dispatchAllowed = capabilities && capabilities.provider_mode !== "DISABLED" && capabilities.allowance_configured;
  return <main className="review-workspace">{seed && <Link className="breadcrumb" href={"/projects/" + seed.project_id}>Project setup</Link>}
    <div className="page-heading"><div><h1>Review every counted object</h1><p>{seed?.question || "Loading source evidence…"}</p></div><span className="count-plaque">Human count <strong>{seed?.expected.value ?? "—"}</strong></span></div>
    <div className="review-summary" aria-live="polite"><span>Detected <strong>{review?.detected_count ?? "Not run"}</strong></span><span>Current masks <strong>{review?.draft_count ?? 0}</strong></span><span>Approved <strong>{review?.approved_count ?? 0}</strong></span>{review?.synthetic && <span className="badge">Synthetic fixture segmentation</span>}</div>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="success" role="status">{notice}</p>}
    {discrepancy && <div className={review.acknowledged ? "success" : "review-warning"}><strong>{review.acknowledged ? "Detection discrepancy reviewed." : review.mismatch ? "Detection count differs from your human count." : "Segmentation needs explicit human review."}</strong><p>{review.acknowledged ? "This exact corrected mask set is approved and acknowledged." : "Correct missing or extra instances, approve each current mask, then acknowledge the discrepancy."} Your human count stays {review.expected_count}.</p></div>}
    <div className="review-layout"><section className="canvas-column"><div className="section-toolbar"><h2>{selected ? "Mask " + (selected.object_ordinal + 1) : "New instance mask"}</h2><label className="checkbox"><input type="checkbox" checked={overlay} onChange={e => setOverlay(e.target.checked)} />Show mask overlays</label></div>
      {seed && <MaskCanvas key={selectedId + ":" + masks.map(mask => mask.mask_asset_id).join(",")} ref={editor} sourceAsset={seed.image_asset_id} masks={masks} selectedId={selectedId} overlay={overlay} onDirty={setDirty} disabled={busy || (job != null && ["QUEUED", "RUNNING"].includes(job.state))} />}
      <p className="muted">Cyan marks the selected instance. Keep each mask on one counted object. Use the brush or the rectangle controls; save before switching instances.</p>
    </section><aside className="review-sidebar"><section className="instance-panel"><h2>Instance masks</h2>{masks.length ? <ol className="instance-list">{masks.map(mask => <li key={mask.id}><button className={selectedId === mask.id ? "instance selected-instance" : "instance"} onClick={() => choose(mask.id)} disabled={busy}><span>Mask {mask.object_ordinal + 1}</span><span className={mask.reviewed ? "reviewed-label" : "muted"}>{mask.reviewed ? "Reviewed" : "Needs review"}</span><small>Revision {mask.revision}</small></button></li>)}</ol> : <p className="muted">Request segmentation or draw the first instance yourself.</p>}<button type="button" className="secondary full-width" onClick={() => choose("")} disabled={busy}>Add missing instance</button></section>
      <section className="form-panel mask-actions"><h2>{selected ? "Review this revision" : "Save a new mask"}</h2>{selected && <p>Revision {selected.revision} · {selected.origin === "HUMAN" ? "Human correction" : selected.origin === "FIXTURE" ? "Synthetic proposal" : "SAM proposal"}{selected.reviewed ? " · Approved by " + selected.reviewer : ""}</p>}
        <label>Object label<input value={objectLabel} onChange={e => setObjectLabel(e.target.value)} minLength={2} maxLength={100} readOnly={!!selected || masks.length > 0} /></label><label>Correction or review notes<textarea value={notes} onChange={e => setNotes(e.target.value)} maxLength={2000} rows={3} placeholder="What did you correct or verify?" /></label>
        {dirty && <p className="unsaved">Unsaved mask correction</p>}<button type="button" className="primary full-width" onClick={save} disabled={busy || !dirty || !notes.trim()}>{selected ? "Save correction" : "Save new mask"}</button>
        {selected && <><button type="button" className="secondary full-width" onClick={approve} disabled={busy || dirty || !notes.trim()}>Approve this revision</button><div className="action-pair"><button type="button" className="quiet" onClick={showHistory} disabled={busy}>Revision history</button><button type="button" className="quiet danger" onClick={retire} disabled={busy || dirty || !notes.trim()}>Retire extra mask</button></div></>}
      </section></aside></div>
    {history && <section className="history-section"><h2>Immutable revision history</h2><div className="history-grid">{history.map(item => <article key={item.revision}><img src={assetURL(item.mask_asset_id)} alt={"Mask revision " + item.revision} /><h3>Revision {item.revision}</h3><p>{item.reviewed ? "Approved by " + item.reviewer : "Not approved"}{item.retired_at ? " · Previous revision" : " · Current revision"}</p><p>{item.notes || "Original proposal"}</p></article>)}</div></section>}
    <div className="review-footer"><section className="form-panel"><h2>Segmentation</h2><label>Object concept<input value={objectLabel} onChange={e => setObjectLabel(e.target.value)} minLength={2} maxLength={100} readOnly={masks.length > 0} /></label>
      <fieldset className="limit-fields"><legend>Explicit job limits</legend><label>Maximum dollars<input type="number" min={0.001} max={150} step={0.001} value={dollars} onChange={e => setDollars(Number(e.target.value))} /></label><label>Maximum calls<input type="number" min={1} max={100} value={calls} onChange={e => setCalls(Number(e.target.value))} /></label><label>Runtime seconds<input type="number" min={60} max={7200} value={runtime} onChange={e => setRuntime(Number(e.target.value))} /></label></fieldset>
      <button type="button" className="secondary" onClick={segment} disabled={busy || dirty || !dispatchAllowed || (job != null && ["QUEUED", "RUNNING"].includes(job.state))}>{review?.segmentation_outcome ? "Request segmentation again" : "Request segmentation"}</button><p className="muted">{capabilities?.provider_mode === "FIXTURE" ? "Synthetic fixture only. Proposals still require your review." : capabilities?.provider_mode === "LIVE" ? "Uses the configured live provider within the stated limits and server allowance." : "Provider dispatch is disabled. You can draw and review masks manually."}</p>{job && <JobStatus key={job.job_id} initial={job} onComplete={completed} />}
    </section><div>{discrepancy && <form className="form-panel" onSubmit={acknowledge}><h2>Acknowledge the discrepancy</h2><p>Detected {review.detected_count ?? "unknown"}; human count {review.expected_count}. Review and correct all {review.expected_count} distinct masks first.</p><label>Discrepancy review notes<textarea required minLength={10} maxLength={2000} value={ackNotes} onChange={e => setAckNotes(e.target.value)} rows={3} /></label><button className="secondary" disabled={busy || dirty || review.draft_count !== review.expected_count || review.approved_count !== review.expected_count || review.acknowledged}>{review.acknowledged ? "Discrepancy acknowledged" : "Acknowledge reviewed mask set"}</button></form>}
      <form className="form-panel contract-panel" onSubmit={createContract}><h2>Create the visual test contract</h2><p>Protects all reviewed instances with a minimum visible fraction of 95%. Permits background replacement, background clutter and brightness changes; forbids adding, removing or repainting protected objects.</p><p className="muted">Future experiment limits: 20 candidates, 60 target calls, $10 and 20 minutes. Saving this contract makes no provider calls.</p>
        {review && review.blocking.length > 0 && <ul className="blocking-list">{review.blocking.map(reason => <li key={reason}>{blockingText[reason] || "Complete the required seed review."}</li>)}</ul>}<button className="primary" disabled={busy || dirty || !review?.can_create_contract}>Create contract</button>{created && <div className="success" role="status"><strong>Contract version {created.version} saved</strong><p>Expected count: {created.contract.expected.value}. The reviewed asset revisions are preserved.</p><p className="hash">SHA-256 {created.contract_sha256}</p></div>}
      </form></div></div></main>;
}
