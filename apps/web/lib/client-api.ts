export type { components } from "@/lib/types/api.generated";
export class ApiError extends Error {
  constructor(message: string, public status: number, public correlationId?: string, public code?: string) {
    super(message + (correlationId ? " Reference: " + correlationId : "")); this.name = "ApiError";
  }
}
async function accepted(response: Response): Promise<Response> {
  if (response.ok) return response;
  const data = await response.json().catch(() => null), envelope = data?.error;
  if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("counterforge:unauthorized"));
  const reference = typeof envelope?.correlation_id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(envelope.correlation_id) ? envelope.correlation_id : undefined;
  const code = typeof envelope?.code === "string" && /^[A-Z0-9_]{1,80}$/.test(envelope.code) ? envelope.code : undefined;
  throw new ApiError(typeof envelope?.message === "string" ? envelope.message.slice(0, 1000) : "The request could not be completed. Reload and try again.", response.status, reference, code);
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await accepted(await fetch("/api/backend/" + path, { ...options, credentials: "same-origin", cache: "no-store" }));
  return response.json() as Promise<T>;
}
export async function eventResponse(path: string, after: number, signal: AbortSignal): Promise<Response> {
  return accepted(await fetch("/api/backend/" + path, { headers: { Accept: "text/event-stream", "Last-Event-ID": String(after) }, credentials: "same-origin", cache: "no-store", signal }));
}
export function jsonPost(value: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(value) };
}
export const assetURL = (id: string) => "/api/backend/assets/" + id + "/content";

export async function allPages<T>(path: string, options: RequestInit = {}): Promise<{ items: T[]; next_cursor: null }> {
  const items: T[] = [], seen = new Set<string>(), signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000);
  let cursor: string | null = null;
  for (let page = 0; page < 100; page++) {
    const data: { items: T[]; next_cursor?: string | null } = await api(path + (cursor ? (path.includes("?") ? "&" : "?") + "cursor=" + encodeURIComponent(cursor) : ""), { ...options, signal });
    items.push(...data.items);
    if (!data.next_cursor) return { items, next_cursor: null };
    if (seen.has(data.next_cursor)) throw new Error("The list could not be loaded completely. Please try again.");
    seen.add(data.next_cursor); cursor = data.next_cursor;
  }
  throw new Error("This list exceeds the workspace display limit. Use a smaller project.");
}
