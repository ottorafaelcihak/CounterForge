"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, allPages, type components } from "@/lib/client-api";

import { RetestPanel } from "./retest-panel";

type ExportRecord = components["schemas"]["ExportRef"];
export const usd = (value: number) => "$" + value.toFixed(6);
export const fixtureLabel = "LOCAL_FIXTURE · simulated evidence";

export function ExportCard({ record: listed, verificationAllowed = true }: { record: ExportRecord; verificationAllowed?: boolean }) {
  const [verified, setVerified] = useState<ExportRecord | null>(null), [error, setError] = useState(""), [checking, setChecking] = useState(false);
  const record = listed.state === "READY" && verified?.export_id === listed.export_id ? verified : listed;
  const ready = verificationAllowed && record.state === "READY" && !!record.links.download;
  async function verifyDownload() {
    setChecking(true); setError("");
    try { setVerified(await api<ExportRecord>("exports/" + listed.export_id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Current evidence could not be verified."); }
    finally { setChecking(false); }
  }
  return <article className="form-panel export-card" data-testid="export-record" data-export-id={record.export_id}>
    <div className="history-heading"><h3>Regression bundle</h3><span className={"badge " + (ready ? "verdict-valid" : record.state === "REVOKED" || record.state === "FAILED" ? "verdict-review" : "")} data-testid="export-state">{record.state === "READY" && !ready ? "REGISTERED" : record.state}</span></div>
    {record.provenance === "LOCAL_FIXTURE" && <p className="review-warning">{fixtureLabel}. This bundle does not establish a real model weakness.</p>}
    <p className="muted">Export <code>{record.export_id}</code>{record.runner_version && <> · Runner {record.runner_version}</>}</p>
    {record.finding_id && <p><Link href={"/findings/" + record.finding_id}>View finding evidence</Link></p>}
    {record.state === "QUEUED" || record.state === "RUNNING" ? <p role="status">Building and verifying the retained evidence. Download becomes available after registration.</p> : null}
    {record.state === "READY" && !ready && <div><p>{verificationAllowed ? "Registered bundle. Verify its current evidence before downloading." : "Current finding evidence is unavailable; download remains blocked."}</p>{verificationAllowed && <button className="secondary" disabled={checking} data-testid="verify-export" onClick={verifyDownload}>{checking ? "Checking evidence…" : "Verify bundle for download"}</button>}</div>}
    {error && <p role="alert" className="error">{error}</p>}
    {record.state === "FAILED" && <p className="error">The bundle could not be verified. {record.error_code || "Reload the finding to inspect the saved evidence before requesting another export."}</p>}
    {record.state === "REVOKED" && <p className="review-warning">Current evidence no longer qualifies this finding for export. Historical bytes remain retained; download is unavailable.</p>}
    {record.bundle_sha256 && <p className="export-checksum">ZIP SHA-256 <code data-testid="bundle-sha256">{record.bundle_sha256}</code></p>}
    {record.manifest_sha256 && <details><summary>Manifest SHA-256</summary><code className="export-checksum">{record.manifest_sha256}</code></details>}
    {ready && <p><a className="button primary button-link" data-testid="download-export" href={"/api/backend/exports/" + record.export_id + "/download"}>Download verified ZIP</a></p>}
    {record.project_id && <RetestPanel exportId={record.export_id} projectId={record.project_id} verified={ready} />}
  </article>;
}

export function RunnerInstructions() {
  return <section className="form-panel runner-instructions"><h2>Run the exported test</h2>
    <p>Extract the ZIP into a new directory and compare its SHA-256 with the value shown above. Verify all members before trusting the extracted runner.</p>
    <pre>sha256sum -c sha256sums.txt</pre>
    <p>Use an isolated Python 3.12 environment outside the extracted bundle. Install its pinned requirements with that environment’s Python.</p>
    <pre>python -m pip install -r requirements.txt</pre>
    <h3>Recorded replay · offline</h3><p>Reads historical responses without calling a provider. A recorded incorrect count still fails the correctness assertion; this is not fresh model reproduction.</p>
    <pre>python runner/target_adapter.py --replay-recorded{"\n"}COUNTERFORGE_RUN_MODE=replay python -m pytest -q tests</pre>
    <h3>Authorized live execution</h3><p>For REAL_PROVIDER bundles, set <code>MODEL_API_KEY</code> in your shell environment, then explicitly opt in. Each invocation is bounded to one provider call; review the manifest’s model, prompt and settings first. Provider behavior may have changed.</p>
    <pre>python runner/target_adapter.py --live</pre>
    <p className="muted">A healthy target passes. An interpretable wrong count fails with expected/actual values. Integrity, transport, refusal and parser failures are execution errors. LOCAL_FIXTURE bundles are development demonstrations.</p>
  </section>;
}

export function RegressionLibrary() {
  const [records, setRecords] = useState<ExportRecord[]>([]), [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const reload = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    let active = true, fetching = false;
    async function refresh() {
      if (!active || fetching) return;
      fetching = true;
      try {
        const result = await allPages<ExportRecord>("regressions?limit=100");
        if (active) { setRecords(result.items); setError(""); setLoaded(true); }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : "The regression library could not be loaded."); }
      finally { fetching = false; }
    }
    reload.current = refresh; void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    const focus = () => void refresh(); window.addEventListener("focus", focus);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", focus); };
  }, []);
  return <main className="workspace regression-workspace"><nav className="evidence-navigation"><Link href="/">Projects</Link><button className="quiet" onClick={() => reload.current()}>Reload library</button></nav>
    <div className="page-heading"><div><h1>Regression library</h1><p>Portable correctness tests backed by retained findings and current validity evidence.</p></div><span className="workspace-label">Owner workspace</span></div>
    {error && <p className="error" role="alert">{error}</p>}
    {!loaded && !error && <p role="status">Loading saved exports…</p>}
    {loaded && !records.length && <section className="empty-state"><h2>No regression bundles yet</h2><p>Open a stable finding, inspect its evidence and request an export. Partial confirmations and unreviewed candidates do not qualify.</p></section>}
    <section className="export-list" aria-label="Saved regression bundles">{records.map(record => <ExportCard key={record.export_id} record={record} />)}</section>
    <RunnerInstructions />
  </main>;
}
