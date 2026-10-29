"use client";
/* Authenticated private images use the same-origin proxy and its session cookie. */
/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { api, allPages, assetURL, jsonPost, type components } from "@/lib/client-api";
import { JobStatus, type Job } from "./job-status";
import { ExperimentSetup } from "./experiment-setup";
type Project = components["schemas"]["Project"];
type Seed = components["schemas"]["Seed"];
type Target = components["schemas"]["Target"];
type Capabilities = components["schemas"]["SetupCapabilities"];
export function ProjectSetup({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null), [seeds, setSeeds] = useState<Seed[]>([]), [targets, setTargets] = useState<Target[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [fixture, setFixture] = useState<File | null>(null), [job, setJob] = useState<Job | null>(null), [adapter, setAdapter] = useState<"LOCAL_FIXTURE" | "META_SPARK">("META_SPARK");
  const [checkSeed, setCheckSeed] = useState(""), [checkTarget, setCheckTarget] = useState("");
  const [dollars, setDollars] = useState(5), [calls, setCalls] = useState(3), [runtime, setRuntime] = useState(120);
  useEffect(() => {
    Promise.all([api<Project>("projects/" + projectId), allPages<Seed>("projects/" + projectId + "/seeds?limit=100"),
      allPages<Target>("projects/" + projectId + "/targets?limit=100"), api<Capabilities>("setup")])
      .then(([p, s, t, c]) => { setProject(p); setSeeds(s.items); setTargets(t.items); setCapabilities(c);
        setCheckSeed(s.items[0]?.id || ""); setCheckTarget(t.items[0]?.id || "");
        setAdapter(c.provider_mode === "FIXTURE" ? "LOCAL_FIXTURE" : "META_SPARK");
      }).catch(e => setError(e.message));
  }, [projectId]);
  async function loadFixture() {
    setError("");
    try {
      const response = await fetch("/api/backend/fixtures/development-seed", { cache: "no-store" });
      if (!response.ok) throw new Error("The development seed is unavailable.");
      setFixture(new File([await response.blob()], "five-blue-containers.png", { type: "image/png" }));
    } catch (e) { setError(e instanceof Error ? e.message : "The fixture could not be loaded."); }
  }
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = event.currentTarget, data = new FormData(form);
    if (fixture) data.set("file", fixture);
    try {
      const seed = await api<Seed>("projects/" + projectId + "/seeds", { method: "POST", body: data });
      setSeeds(items => [...items, seed]); setCheckSeed(seed.id); form.reset(); setFixture(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Seed could not be uploaded."); }
    finally { setBusy(false); }
  }
  async function createTarget(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const target = await api<Target>("projects/" + projectId + "/targets", jsonPost({
        adapter_key: adapter, model_id: adapter === "META_SPARK" ? "muse-spark-1.3" : "LOCAL_FIXTURE",
        model_revision: adapter === "META_SPARK" ? "provider-managed" : "fixture-v1",
        prompt_template: data.get("prompt"), inference_options: { temperature: 0, top_p: null, max_output_tokens: 64 },
      }));
      setTargets(items => [...items, target]); setCheckTarget(target.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Target could not be saved."); }
    finally { setBusy(false); }
  }
  async function check() {
    setError(""); setBusy(true); setJob(null);
    try { setJob(await api<Job>("targets/" + checkTarget + "/check", jsonPost({ seed_id: checkSeed,
      limits: { max_estimated_usd: dollars, max_calls: calls, max_runtime_seconds: runtime } }))); }
    catch (e) { setError(e instanceof Error ? e.message : "Target check could not be queued."); }
    finally { setBusy(false); }
  }
  return <main className="workspace"><Link href="/" className="breadcrumb">Projects</Link><div className="page-heading"><div><h1>{project?.name || "Project setup"}</h1><p>{project?.description || "Prepare your source evidence, then review the object masks."}</p></div><span className="badge">{capabilities?.provider_mode === "FIXTURE" ? "Synthetic fixture mode" : capabilities?.provider_mode === "LIVE" ? "Live calls gated" : "Provider calls disabled"}</span></div>
    {error && <p className="error" role="alert">{error}</p>}
    <ExperimentSetup projectId={projectId} setupRevision={seeds.length + ":" + targets.length} />
    <section className="seed-section"><h2>Source seeds</h2>{seeds.length ? <div className="seed-grid">{seeds.map(seed => <article className="seed-tile" key={seed.id}><img loading="lazy" src={assetURL(seed.image_asset_id)} alt={"Source image: " + seed.question} /><div><h3>{seed.question}</h3><p>Human count <strong>{seed.expected.value}</strong> · Reviewer {seed.expected.reviewer}</p><Link className="button primary" href={"/seeds/" + seed.id + "/masks"}>Review masks</Link></div></article>)}</div> : <p className="empty-state">Upload a rights-cleared image to begin.</p>}</section>
    <div className="setup-columns"><form className="form-panel" onSubmit={upload}><h2>Add a source seed</h2><label>Source image<input name="file" type="file" accept="image/png,image/jpeg,image/webp" required={!fixture} onChange={() => setFixture(null)} /></label>
      {capabilities?.synthetic_fixture_enabled && <div className="fixture-picker"><button type="button" className="secondary" onClick={loadFixture}>Load development seed</button><p className="muted">Original CC0 illustration with five containers. Synthetic test material; inspect it and confirm the count yourself.</p>{fixture && <p role="status">Selected {fixture.name}</p>}</div>}
      <label>Counting question<input name="question" required minLength={10} maxLength={300} defaultValue="How many blue containers are visible? Return only an integer." /></label>
      <div className="form-row"><label>Human count<input name="expected_count" type="number" required min={1} max={16} defaultValue={5} /></label><label>Label reviewer<input name="label_reviewer" required maxLength={100} placeholder="Your name" /></label></div>
      <label className="checkbox"><input name="license_ack" type="checkbox" value="true" required />I have rights to use this image and have verified its count.</label><button className="primary" disabled={busy}>Upload seed</button>
    </form><div><form className="form-panel" onSubmit={createTarget}><h2>Configure a target</h2><label>Target model<select aria-label="Target model" value={adapter} onChange={event => setAdapter(event.target.value as typeof adapter)}><option value="META_SPARK">Meta Spark 1.3</option>{capabilities?.synthetic_fixture_enabled && <option value="LOCAL_FIXTURE">Local fixture (synthetic)</option>}</select></label><label>Target instruction<textarea name="prompt" required minLength={10} maxLength={300} rows={3} defaultValue="Count the blue containers in the image. Return only one integer." /></label><p className="muted">Pinned settings: temperature 0, output limit 64 tokens. Live model revision is provider-managed.</p><button className="secondary" disabled={busy}>Save target</button></form>
    <section className="form-panel target-check"><h2>Check a target</h2><p>This is one connection check. It does not run the experiment baseline.</p><label>Seed for target check<select aria-label="Seed for target check" value={checkSeed} onChange={event => setCheckSeed(event.target.value)}><option value="">Choose a seed</option>{seeds.map(seed => <option key={seed.id} value={seed.id}>{seed.question}</option>)}</select></label><label>Saved target<select aria-label="Saved target" value={checkTarget} onChange={event => setCheckTarget(event.target.value)}><option value="">Choose a target</option>{targets.map(target => <option key={target.id} value={target.id}>{target.model_id} · {target.model_revision}</option>)}</select></label>
      <fieldset className="limit-fields"><legend>Explicit job limits</legend><label>Maximum dollars<input type="number" min={0.001} max={150} step={0.001} value={dollars} onChange={e => setDollars(Number(e.target.value))} /></label><label>Maximum calls<input type="number" min={1} max={100} value={calls} onChange={e => setCalls(Number(e.target.value))} /></label><label>Runtime seconds<input type="number" min={60} max={7200} value={runtime} onChange={e => setRuntime(Number(e.target.value))} /></label></fieldset>
      <button type="button" className="secondary" onClick={check} disabled={busy || !checkSeed || !checkTarget || capabilities?.provider_mode === "DISABLED" || !capabilities?.allowance_configured}>Check target</button>{!capabilities?.allowance_configured && <p className="muted">Configure an operator allowance on the server before dispatching provider jobs.</p>}{job && <JobStatus initial={job} key={job.job_id} />}</section></div></div></main>;
}
