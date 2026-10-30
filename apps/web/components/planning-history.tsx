import type { components } from "@/lib/client-api";

type Run = components["schemas"]["ExperimentView"];
const money = (value: number) => "$" + value.toFixed(6);

export function PlanningHistory({ run }: { run: Run }) {
  const batches = run.planning_batches || [];
  const history = run.planner_history || [];
  if (run.search_policy !== "FEEDBACK_V1") return null;
  return <section aria-label="Feedback planning evidence" className="planning-history">
    <h2>Feedback planning evidence</h2>
    <p>Policy feedback-v1 uses observed outcomes and reserves every fifth attempt, starting with the first, for seeded random exploration. Candidate attempts include duplicates and rejected selections.</p>
    <p className="muted">Scores and smoothed estimates are engineering defaults. Evidence of better failure discovery requires the later evaluation.</p>
    {batches.length ? batches.map(batch => <details className="form-panel" key={batch.slot_index}>
      <summary>Attempt {batch.slot_index + 1} · {batch.slot_strategy.replaceAll("_", " ").toLowerCase()} · {batch.author.replaceAll("_", " ")}</summary>
      <p>Policy {batch.policy_revision} · template {batch.template_revision} · random seed {batch.random_seed}</p>
      {batch.fallback_reason && <p className="review-warning">Fallback: {batch.fallback_reason.replaceAll("_", " ")}</p>}
      <p>Random exploration: {batch.exploration_used} used / {batch.exploration_reserved} reserved. Remaining candidate attempts: {batch.remaining_budget.max_candidates}.</p>
      <details><summary>Outcomes supplied to this batch ({batch.history_snapshot.length})</summary><ul>{batch.history_snapshot.map((item, index) => <li key={index}>{item.family.replaceAll("_", " ")} · {item.strength} · {item.validity || "Unreviewed"}<p>{item.hypothesis}</p><p>{(item.target_outcomes || []).map(outcome => outcome.phase + ": " + outcome.outcome).join("; ") || "No target observation"}</p></li>)}</ul></details>
      <div className="table-scroll" role="region" aria-label="Scrollable proposal rankings" tabIndex={0}><table><caption>Proposed and selected variations</caption><thead><tr><th scope="col">Proposal</th><th scope="col">Selection</th><th scope="col">Score</th><th scope="col">Cost estimate</th><th scope="col">Components</th></tr></thead>
        <tbody>{batch.rankings.map(proposal => <tr key={proposal.proposal_order}>
          <td>{proposal.family.replaceAll("_", " ")} · strength {proposal.strength}<p>{proposal.hypothesis}</p></td>
          <td>{proposal.selected ? "Selected" : proposal.rejection_reason || "Unselected"}{proposal.rank != null && <small> · rank {proposal.rank}</small>}</td>
          <td>{proposal.score.toFixed(4)}</td><td>{money(proposal.estimated_cost_usd)}</td>
          <td>Validity {proposal.p_valid.toFixed(3)} · failure {proposal.p_failure.toFixed(3)} · exploration {proposal.explore.toFixed(3)} · {proposal.family_trials} family trials</td>
        </tr>)}</tbody></table></div>
    </details>) : <p>No planning batch has been retained yet.</p>}
    <details className="form-panel"><summary>Recent retained outcomes ({history.length})</summary>
      <p className="muted">The planner receives a bounded recent window. Unknown replies remain explicit in the retained evidence.</p>
      {history.length ? <ol>{history.map((item, index) => <li key={index}>
        <strong>{item.family.replaceAll("_", " ")} · {item.strength} · {item.validity || "Unreviewed"}</strong>
        <p>{item.hypothesis}</p><p>{(item.target_outcomes || []).map(outcome => outcome.phase + ": " + outcome.outcome).join("; ") || "No target observation"} · {money(item.cost_usd)} cost exposure</p>
        {!!item.reason_codes?.length && <p>Reasons: {item.reason_codes.join(", ")}</p>}
      </li>)}</ol> : <p>No candidate outcome is available yet.</p>}
    </details>
  </section>;
}
