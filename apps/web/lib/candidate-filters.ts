import type { components } from "./types/api.generated";
type Candidate = components["schemas"]["Mutation"];
export type CandidateFilter = "ALL" | NonNullable<Candidate["validation"]>["verdict"] | "UNKNOWN";
export function filterCandidates(items: Candidate[], verdict: CandidateFilter, family: string, state: string): Candidate[] {
  return items.filter(item => (!family || item.family === family) && (!state || item.state === state) && (
    verdict === "ALL" || (verdict === "UNKNOWN" ? !item.validation || item.validation.checks.some(check => check.status === "UNKNOWN") :
      (item.validation?.verdict || "REVIEW_REQUIRED") === verdict)
  ));
}
