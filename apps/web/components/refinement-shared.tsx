import type { components } from "@/lib/client-api";

export function JobCosts({ usage }: { usage?: components["schemas"]["JobUsage"] }) {
  const amount = (value?: number) => "$" + (value || 0).toFixed(6);
  return <p className="muted">Actual {amount(usage?.actual_usd)} · estimated {amount(usage?.estimated_usd)} · unresolved {amount(usage?.unresolved_usd)} · reserved {amount(usage?.reserved_usd)}</p>;
}

export const actionError = (value: unknown) => value instanceof Error ? value.message : "The request could not be completed. Reload the saved status before trying again.";
