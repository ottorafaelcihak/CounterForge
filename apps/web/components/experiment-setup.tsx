"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, allPages, jsonPost, type components } from "@/lib/client-api";

type Seed = components["schemas"]["Seed"];
type Contract = components["schemas"]["Contract"];
type Target = components["schemas"]["Target"];
type Run = components["schemas"]["ExperimentView"];
type Start = components["schemas"]["StartExperiment"];
type Capabilities = components["schemas"]["SetupCapabilities"];

export function ExperimentSetup({ projectId, setupRevision }: { projectId: string; setupRevision: string }) {
  const router = useRouter();
  const [contracts, setContracts] = useState<{ seed: Seed; contract: Contract }[]>([]);
  const [targets, setTargets] = useState<Target[]>([]), [runs, setRuns] = useState<Run[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [contractId, setContractId] = useState(""), [targetId, setTargetId] = useState("");
  const [mode, setMode] = useState<Start["mode"]>("ADAPTIVE");
  const [policy, setPolicy] = useState<Start["search_policy"]>("ONE_SHOT");
  const [randomSeed, setRandomSeed] = useState("20261029");
  const [limits, setLimits] = useState({ max_candidates: 3, max_target_calls: 8, max_estimated_usd: 10, max_runtime_seconds: 1200 });
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const pending = useRef<{ body: string; key: string } | null>(null);
  const currentContract = useRef("");
  const requestNo = useRef(0);
  const mounted = useRef(false);
  const selected = contracts.find(item => item.contract.id === contractId);

  const load = useCallback(async () => {
    const sequence = ++requestNo.current;
    try {
      const [seeds, available, previous, safe] = await Promise.all([
        allPages<Seed>("projects/" + projectId + "/seeds?limit=100"),
        allPages<Target>("projects/" + projectId + "/targets?limit=100"),
        allPages<Run>("projects/" + projectId + "/experiments?limit=100"), api<Capabilities>("setup"),
      ]);
      const groups = await Promise.all(seeds.items.map(async seed => ({ seed,
        result: await allPages<Contract>("seeds/" + seed.id + "/contracts?limit=100"),
      })));
      if (!mounted.current || sequence !== requestNo.current) return;
      const choices = groups.flatMap(({ seed, result }) => result.items.map(contract => ({ seed, contract })));
      setContracts(choices); setTargets(available.items.filter(target => target.enabled)); setRuns(previous.items); setCapabilities(safe);
      const chosen = choices.find(item => item.contract.id === currentContract.current) || choices[0];
      currentContract.current = chosen?.contract.id || "";
      setContractId(currentContract.current);
      if (chosen) {
        const cap = chosen.contract.contract.limits;
        setLimits(old => ({ max_candidates: Math.min(old.max_candidates, cap.max_candidates),
          max_target_calls: Math.min(old.max_target_calls, cap.max_target_calls), max_estimated_usd: Math.min(old.max_estimated_usd, cap.max_estimated_usd),
          max_runtime_seconds: Math.min(old.max_runtime_seconds, cap.max_runtime_seconds) }));
      }
      setTargetId(old => available.items.some(item => item.id === old && item.enabled) ? old : available.items.find(item => item.enabled)?.id || "");
      setError("");
    } catch (e) { if (mounted.current && sequence === requestNo.current) setError(e instanceof Error ? e.message : "Experiment setup could not be loaded."); }
    finally { if (mounted.current && sequence === requestNo.current) setLoading(false); }
  }, [projectId]);
  useEffect(() => {
    let active = true;
    mounted.current = true;
    void Promise.resolve().then(() => { if (active) void load(); });
    return () => { active = false; mounted.current = false; };
  }, [load, setupRevision]);
  useEffect(() => { const focus = () => void load(); window.addEventListener("focus", focus); return () => window.removeEventListener("focus", focus); }, [load]);

  function changeContract(id: string) {
    currentContract.current = id;
    setContractId(id);
    const cap = contracts.find(item => item.contract.id === id)?.contract.contract.limits;
    if (cap) setLimits(old => ({ max_candidates: Math.min(old.max_candidates, cap.max_candidates), max_target_calls: Math.min(old.max_target_calls, cap.max_target_calls),
      max_estimated_usd: Math.min(old.max_estimated_usd, cap.max_estimated_usd), max_runtime_seconds: Math.min(old.max_runtime_seconds, cap.max_runtime_seconds) }));
  }
  const ceiling = selected?.contract.contract.limits;
  const usesSeed = mode === "RANDOM_BASELINE" || policy === "FEEDBACK_V1";
  const seedValue = Number(randomSeed);
  const seedValid = !usesSeed || randomSeed.trim() !== "" && Number.isSafeInteger(seedValue) && seedValue >= 0;
  const valid = seedValid && ceiling && Object.entries(limits).every(([key, value]) => Number.isFinite(value) && value > 0 && value <= ceiling[key as keyof typeof ceiling]) &&
    Number.isInteger(limits.max_candidates) && Number.isInteger(limits.max_target_calls) && limits.max_target_calls >= 3 &&
    Number.isInteger(limits.max_runtime_seconds) && limits.max_runtime_seconds >= 60;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !valid || !contractId || !targetId) return;
    setBusy(true); setError("");
    const body: Start = { project_id: projectId, contract_id: contractId, target_config_id: targetId, mode, search_policy: mode === "ADAPTIVE" ? policy : "ONE_SHOT", limits, ...(usesSeed ? { random_seed: seedValue } : {}) };
    const canonical = JSON.stringify(body);
    if (!pending.current || pending.current.body !== canonical) pending.current = { body: canonical, key: crypto.randomUUID() };
    try {
      const result = await api<components["schemas"]["ExperimentRef"]>("experiments", { ...jsonPost(body), headers: {
        "Content-Type": "application/json", "Idempotency-Key": pending.current.key,
      } });
      router.push("/experiments/" + result.experiment_id);
    } catch (e) { setError(e instanceof Error ? e.message : "The start request could not be confirmed. Retry the unchanged form safely."); }
    finally { setBusy(false); }
  }

  return <section className="experiment-setup"><div className="section-toolbar"><h2>Bounded experiments</h2><button type="button" className="quiet" onClick={() => { setLoading(true); void load(); }} disabled={busy || loading}>Reload run setup</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="setup-columns"><form className="form-panel" onSubmit={submit}>
      <h3>Start an experiment</h3><p>Runs three fresh seed checks, plans a controlled variation and pauses for candidate review before target testing.</p>
      <label><span id="run-contract-label">Reviewed contract</span><select aria-labelledby="run-contract-label" value={contractId} onChange={e => changeContract(e.target.value)} disabled={busy || loading} required>
        <option value="">Choose an approved contract</option>{contracts.map(({ seed, contract }) => <option key={contract.id} value={contract.id}>{seed.question} · v{contract.version} · count {contract.contract.expected.value}</option>)}
      </select></label>
      <label><span id="run-target-label">Experiment target</span><select aria-labelledby="run-target-label" value={targetId} onChange={e => setTargetId(e.target.value)} disabled={busy || loading} required>
        <option value="">Choose a target</option>{targets.map(target => <option key={target.id} value={target.id}>{target.model_id} · {target.model_revision}</option>)}
      </select></label>
      <label><span id="run-mode-label">Planning mode</span><select aria-labelledby="run-mode-label" value={mode} onChange={e => { setMode(e.target.value as typeof mode); setPolicy("ONE_SHOT"); }} disabled={busy}>
        <option value="ADAPTIVE">Initial Spark plan</option><option value="RANDOM_BASELINE">Seeded random baseline</option>
      </select></label>
      {mode === "ADAPTIVE" && <label><span id="search-policy-label">Search policy</span><select aria-labelledby="search-policy-label" value={policy} onChange={e => setPolicy(e.target.value as typeof policy)} disabled={busy}>
        <option value="ONE_SHOT">One-shot plan</option><option value="FEEDBACK_V1">Feedback search · policy feedback-v1</option>
      </select></label>}
      {usesSeed && <label>Recorded random seed<input aria-label="Recorded random seed" type="number" min={0} max={Number.MAX_SAFE_INTEGER} step={1} value={randomSeed} onChange={e => setRandomSeed(e.target.value)} disabled={busy} required /></label>}
      {policy === "FEEDBACK_V1" && <p className="muted">Later Spark proposals use retained outcomes. {Math.ceil(limits.max_candidates / 5)} candidate attempts are reserved for seeded random exploration. Duplicates and rejected selections consume attempts; scores are engineering estimates.</p>}
      <fieldset className="limit-fields"><legend>Maximum run limits</legend>
        <label>Candidates<input aria-label="Maximum candidates" type="number" min={1} max={ceiling?.max_candidates || 100} required value={limits.max_candidates} onChange={e => setLimits(old => ({ ...old, max_candidates: Number(e.target.value) }))} disabled={busy} /></label>
        <label>Target calls<input aria-label="Maximum target calls" type="number" min={3} max={ceiling?.max_target_calls || 500} required value={limits.max_target_calls} onChange={e => setLimits(old => ({ ...old, max_target_calls: Number(e.target.value) }))} disabled={busy} /></label>
        <label>Dollars<input aria-label="Maximum run dollars" type="number" min={0.001} step={0.001} max={ceiling?.max_estimated_usd || 150} required value={limits.max_estimated_usd} onChange={e => setLimits(old => ({ ...old, max_estimated_usd: Number(e.target.value) }))} disabled={busy} /></label>
        <label>Seconds<input aria-label="Maximum run seconds" type="number" min={60} max={ceiling?.max_runtime_seconds || 7200} required value={limits.max_runtime_seconds} onChange={e => setLimits(old => ({ ...old, max_runtime_seconds: Number(e.target.value) }))} disabled={busy} /></label>
      </fieldset>
      {selected && <p className="muted">Fixed human count {selected.contract.contract.expected.value}. Limits can only tighten the reviewed contract. The clock includes paused time.</p>}
      <p className={capabilities?.provider_mode === "LIVE" ? "review-warning" : "muted"}>{capabilities?.provider_mode === "LIVE" ? "Starting dispatches paid Meta requests within these run limits. Unresolved charges remain counted." : capabilities?.provider_mode === "FIXTURE" ? "Synthetic provider fixtures: no real model result or billed usage." : "Provider dispatch is disabled in this workspace."}</p>
      <button className="primary" type="submit" disabled={busy || loading || !valid || !contractId || !targetId || capabilities?.provider_mode === "DISABLED"}>{busy ? "Starting…" : "Start bounded experiment"}</button>
    </form><section className="form-panel"><h3>Previous runs</h3>{runs.length ? <ul className="run-list">{runs.map(run => <li key={run.id}><Link href={"/experiments/" + run.id}><strong>{run.state.replaceAll("_", " ")}</strong><span>{run.id.slice(0, 8)} · {run.metrics.total_target_calls} target calls</span></Link></li>)}</ul> : <p className="muted">Runs appear here after they are started.</p>}</section></div>
  </section>;
}
