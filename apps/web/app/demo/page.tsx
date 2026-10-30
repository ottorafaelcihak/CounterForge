/* eslint-disable @next/next/no-img-element -- Serve approved snapshot bytes without image optimization. */
import Link from "next/link";
import { redirect } from "next/navigation";
import { demoStateLabel, findingAssets, isFixturePreview, publicDemoAssetURL, type DemoManifest } from "@/lib/public-demo";

export const dynamic = "force-dynamic";

async function loadManifest(): Promise<DemoManifest | null> {
  try {
    const base = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") return null;
    const response = await fetch(new URL("/api/v1/public/demo", base), { cache: "no-store", signal: AbortSignal.timeout(10000) });
    if (!response.ok) return null;
    return (await response.json()) as DemoManifest;
  } catch {
    return null;
  }
}

export default async function DemoPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (Object.keys(await searchParams).length) redirect("/demo");
  const manifest = await loadManifest();
  return (
    <>
      <header className="app-header">
        <Link href="/demo" className="brand"><span aria-hidden="true" className="brand-mark">◧</span> CounterForge</Link>
        <span className="header-context">Public recorded demo</span>
        <Link href="/" className="quiet">Owner workspace</Link>
      </header>
      <main className="workspace demo-page" id="demo-content">
        <div className="page-heading">
          <div>
            <h1>Recorded visual-test demo</h1>
            <p>A read-only replay of an explicitly approved snapshot. Nothing on this page can start work or call a model.</p>
          </div>
          {manifest && <span className="badge">{demoStateLabel(manifest)}</span>}
        </div>
        {!manifest ? (
          <div className="empty-state" role="status">
            <h2>Demo unavailable</h2>
            <p>The public demo service could not be reached. No private workspace data is shown here; try again later.</p>
          </div>
        ) : (
          <>
            {isFixturePreview(manifest) && (
              <p className="review-warning" role="note">
                <strong>Local fixture preview.</strong> These results use a local fixture target and demonstrate the
                pipeline only. They are not evidence of live foundation-model failures or measured improvement.
              </p>
            )}
            {manifest.state === "EMPTY" && (
              <div className="empty-state" role="status">
                <h2>No approved snapshot yet</h2>
                <p>
                  A snapshot is prepared privately from retained evidence and published only after an explicit
                  operator approval. Until then this demo stays empty rather than showing unapproved evidence.
                </p>
              </div>
            )}
            {manifest.metrics && (
              <section aria-labelledby="demo-metrics">
                <h2 id="demo-metrics">Recorded run counts</h2>
                <dl className="metrics-grid">
                  <div><dt>Generated candidates</dt><dd>{manifest.metrics.generated_candidates}</dd></div>
                  <div><dt>Valid candidates</dt><dd>{manifest.metrics.valid_candidates}</dd></div>
                  <div><dt>Invalid candidates</dt><dd>{manifest.metrics.invalid_candidates}</dd></div>
                  <div><dt>Review-required candidates</dt><dd>{manifest.metrics.review_required_candidates}</dd></div>
                  <div><dt>Valid screening calls</dt><dd>{manifest.metrics.valid_screening_calls}</dd></div>
                  <div><dt>Total target calls</dt><dd>{manifest.metrics.total_target_calls}</dd></div>
                  <div><dt>Approved findings in this snapshot</dt><dd>{manifest.metrics.stable_findings}</dd></div>
                </dl>
                <p className="muted">Total target calls include seed baseline and minimization retest calls. Canonical screening counts exclude both. Approved findings count only this snapshot.</p>
              </section>
            )}
            {manifest.state !== "EMPTY" && (manifest.findings ?? []).length === 0 && (
              <div className="empty-state" role="status">
                <h2>No confirmed finding in this recorded sample</h2>
                <p>This recorded run produced no stable finding that met the demo eligibility gates. No finding has been invented for display.</p>
              </div>
            )}
            {(manifest.assets ?? []).filter((asset) => !(manifest.findings ?? []).some((finding) => (finding.asset_ids ?? []).includes(asset.id))).length > 0 && (
              <section aria-labelledby="demo-sample-image">
                <h2 id="demo-sample-image">Recorded sample image</h2>
                <div className="evidence-gallery">
                  {(manifest.assets ?? []).filter((asset) => !(manifest.findings ?? []).some((finding) => (finding.asset_ids ?? []).includes(asset.id))).map((asset) => {
                    const url = publicDemoAssetURL(asset.id);
                    return url ? (
                      <figure key={asset.id}>
                        <img src={url} alt="Recorded seed image for this sample" width={asset.width} height={asset.height} />
                        <figcaption>Seed image · SHA-256 {asset.sha256.slice(0, 12)}…</figcaption>
                      </figure>
                    ) : null;
                  })}
                </div>
              </section>
            )}
            {(manifest.findings ?? []).length > 0 && (
              <section aria-labelledby="demo-findings">
                <h2 id="demo-findings">Approved findings</h2>
                {(manifest.findings ?? []).map((finding) => (
                  <article key={finding.id} className="demo-finding">
                    <h3>Stable finding — {finding.mutation_family.replaceAll("_", " ")}</h3>
                    <p>
                      Target model {finding.model_id} answered incorrectly in {finding.confirmation_incorrect} of{" "}
                      {finding.confirmation_attempts} confirmation attempts on an image whose human-verified count
                      is {finding.ground_truth_count}. Mutation strength {finding.mutation_strength}.
                    </p>
                    <p className="muted">{finding.qualification}</p>
                    <div className="evidence-gallery">
                      {findingAssets(manifest, finding).map((asset) => {
                        const url = publicDemoAssetURL(asset.id);
                        return url ? (
                          <figure key={asset.id}>
                            {/* Public snapshot bytes only; IDs are snapshot-local. */}
                            <img src={url} alt={asset.role === "ORIGINAL" ? "Original recorded image before mutation" : "Recorded mutated image shown to the target model"} width={asset.width} height={asset.height} />
                            <figcaption>{asset.role === "ORIGINAL" ? "Original" : "Mutated"} · SHA-256 {asset.sha256.slice(0, 12)}…</figcaption>
                          </figure>
                        ) : null;
                      })}
                    </div>
                  </article>
                ))}
              </section>
            )}
            <section aria-labelledby="demo-architecture">
              <h2 id="demo-architecture">How this evidence was produced</h2>
              <ul>{(manifest.architecture ?? []).map((line) => <li key={line}>{line}</li>)}</ul>
            </section>
            <section aria-labelledby="demo-limitations">
              <h2 id="demo-limitations">Limitations</h2>
              <ul>{(manifest.limitations ?? []).map((line) => <li key={line}>{line}</li>)}</ul>
            </section>
          </>
        )}
      </main>
    </>
  );
}
