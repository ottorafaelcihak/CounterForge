export type { components } from "@/lib/types/api.generated";
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); this.name = "ApiError"; } }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch("/api/backend/" + path, { ...options, cache: "no-store" });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("counterforge:unauthorized"));
    throw new ApiError(data?.error?.message || "The request could not be completed.", response.status);
  }
  return response.json() as Promise<T>;
}
export function jsonPost(value: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(value) };
}
export const assetURL = (id: string) => "/api/backend/assets/" + id + "/content";

export async function allPages<T>(path: string): Promise<{ items: T[]; next_cursor: null }> {
  const items: T[] = [], seen = new Set<string>(), signal = AbortSignal.timeout(30000);
  let cursor: string | null = null;
  for (let page = 0; page < 100; page++) {
    const data: { items: T[]; next_cursor?: string | null } = await api(path + (cursor ? (path.includes("?") ? "&" : "?") + "cursor=" + encodeURIComponent(cursor) : ""), { signal });
    items.push(...data.items);
    if (!data.next_cursor) return { items, next_cursor: null };
    if (seen.has(data.next_cursor)) throw new Error("The list could not be loaded completely. Please try again.");
    seen.add(data.next_cursor); cursor = data.next_cursor;
  }
  throw new Error("This list exceeds the workspace display limit. Use a smaller project.");
}
