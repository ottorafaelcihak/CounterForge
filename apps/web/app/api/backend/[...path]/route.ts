import { NextRequest } from "next/server";
import { authenticated, boundedBody, errorResponse, sameOrigin } from "@/lib/server-session";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };
const id = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const readPaths = new RegExp("^(?:setup|fixtures/development-seed|projects(?:/" + id + "(?:/seeds|/targets|/experiments)?)?|seeds/" + id + "(?:/masks(?:/" + id + "/revisions)?|/mask-review|/contracts)?|assets/" + id + "/content|jobs/" + id + "|contracts/" + id + "|experiments/" + id + "(?:/(?:mutations|findings|metrics|event-history))?|mutations/" + id + "(?:/history)?)$");
const writePaths = new RegExp("^(?:projects(?:/" + id + "/(?:seeds|targets))?|targets/" + id + "/check|seeds/" + id + "/(?:segment|contracts|masks(?:/acknowledgements|/" + id + "/(?:revisions|approve|retire))?)|experiments(?:/" + id + "/(?:pause|resume|cancel))?|findings/" + id + "/(?:confirm|demo-approve)|mutations/" + id + "/review)$");
async function proxy(request: NextRequest, context: Context) {
  if (!authenticated(request)) return errorResponse("Owner authentication is required.", 401);
  if (request.method !== "GET" && !sameOrigin(request)) return errorResponse("This request must come from the configured app origin.", 403);
  const { path } = await context.params;
  if (!path.length || path.some(part => !/^[a-zA-Z0-9_-]+$/.test(part))) return errorResponse("Route was not found.", 404);
  const relative = path.join("/");
  if (!(request.method === "GET" ? readPaths : writePaths).test(relative)) return errorResponse("Route was not found.", 404);
  const query = request.nextUrl.searchParams;
  const allowedQuery = relative.endsWith("/event-history") ? ["limit", "after"] : ["limit", "cursor"];
  if ([...query.keys()].some(key => !allowedQuery.includes(key)) ||
      (query.has("after") && !/^(?:0|[1-9][0-9]{0,17})$/.test(query.get("after")!)) ||
      (query.has("limit") && !/^(?:[1-9][0-9]?|100)$/.test(query.get("limit")!)) ||
      (query.get("cursor")?.length || 0) > 512) return errorResponse("Query could not be accepted.", 422);
  try {
    const base = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new Error("CONFIG");
    const url = new URL("/api/v1/" + relative, base);
    url.search = query.toString();
    const headers = new Headers({ Authorization: "Bearer " + (process.env.OWNER_AUTH_TOKEN || "") });
    const contentType = request.headers.get("content-type");
    if (contentType && contentType.length <= 200) headers.set("Content-Type", contentType);
    const key = request.headers.get("idempotency-key");
    if (key) {
      if (!/^[\x21-\x7e]{1,128}$/.test(key)) return errorResponse("Idempotency key could not be accepted.", 422);
      headers.set("Idempotency-Key", key);
    }
    const data = request.method === "GET" ? undefined : await boundedBody(request.body, 11 * 1024 * 1024);
    const upstream = await fetch(url, { method: request.method, headers,
      body: data?.length ? Buffer.from(data) : undefined, cache: "no-store",
      redirect: "manual", signal: AbortSignal.timeout(30000) });
    if (upstream.status >= 300 && upstream.status < 400) return errorResponse("The API returned an unsupported redirect.", 502);
    // Normalized RGB PNGs can exceed the original 10 MiB compressed upload.
    const responseLimit = relative.startsWith("assets/") ? 64 * 1024 * 1024 : 16 * 1024 * 1024;
    const output = await boundedBody(upstream.body, responseLimit);
    const mediaType = upstream.headers.get("content-type")?.split(";")[0] || "";
    if (!["application/json", "image/png", "image/jpeg", "image/webp"].includes(mediaType)) return errorResponse("The API returned unsupported content.", 502);
    return new Response(Buffer.from(output), { status: upstream.status, headers: {
      "Content-Type": mediaType, "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
    } });
  } catch (error) {
    return errorResponse(error instanceof Error && error.message === "BODY_LIMIT" ? "Content exceeds the size limit." : "The API is unavailable. Check the API service and try again.", error instanceof Error && error.message === "BODY_LIMIT" ? 413 : 503);
  }
}
export const GET = proxy;
export const POST = proxy;
