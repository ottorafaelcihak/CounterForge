import { NextRequest } from "next/server";
import { authenticated, boundedBody, errorResponse } from "@/lib/server-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ id: string }> };
const canonicalId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function GET(request: NextRequest, context: Context) {
  if (!authenticated(request)) return errorResponse("Owner authentication is required.", 401);
  const { id } = await context.params;
  if (!canonicalId.test(id)) return errorResponse("Route was not found.", 404);
  const after = request.headers.get("last-event-id");
  if ([...request.nextUrl.searchParams.keys()].length || (after !== null && !/^(?:0|[1-9][0-9]{0,17})$/.test(after)))
    return errorResponse("Event cursor could not be accepted.", 422);
  try {
    const base = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new Error("CONFIG");
    const headers = new Headers({ Authorization: "Bearer " + (process.env.OWNER_AUTH_TOKEN || ""), Accept: "text/event-stream" });
    if (after !== null) headers.set("Last-Event-ID", after);
    const upstream = await fetch(new URL("/api/v1/experiments/" + id + "/events", base), {
      headers, cache: "no-store", redirect: "manual",
      // Renew authorization on reconnect; cancellation closes the upstream too.
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(60000)]),
    });
    if (!upstream.ok) {
      const bytes = await boundedBody(upstream.body, 64 * 1024);
      if (upstream.headers.get("content-type")?.split(";")[0] === "application/json" && [401, 403, 404, 409, 422, 429, 503].includes(upstream.status))
        return new Response(Buffer.from(bytes), { status: upstream.status, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" } });
      return errorResponse("Saved events are unavailable.", 502);
    }
    if (!upstream.body || upstream.headers.get("content-type")?.split(";")[0] !== "text/event-stream")
      return errorResponse("The API returned an unsupported event stream.", 502);
    return new Response(upstream.body, { headers: {
      "Content-Type": "text/event-stream", "Cache-Control": "private, no-store, no-transform", "Content-Encoding": "identity", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
    } });
  } catch { return errorResponse("Saved events are unavailable. Reconnect to refresh the snapshot.", 503); }
}
