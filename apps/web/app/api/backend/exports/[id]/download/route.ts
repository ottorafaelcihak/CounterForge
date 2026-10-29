import { NextRequest } from "next/server";
import { authenticated, boundedBody, errorResponse } from "@/lib/server-session";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ id: string }> };
export async function GET(request: NextRequest, context: Context) {
  if (!authenticated(request)) return errorResponse("Owner authentication is required.", 401);
  const { id } = await context.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) return errorResponse("Route was not found.", 404);
  if (request.nextUrl.search) return errorResponse("Query could not be accepted.", 422);
  try {
    const base = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new Error("CONFIG");
    const response = await fetch(new URL("/api/v1/exports/" + id + "/download", base), {
      headers: { Authorization: "Bearer " + (process.env.OWNER_AUTH_TOKEN || "") },
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(30000),
    });
    if (response.status >= 300 && response.status < 400) return errorResponse("The API returned an unsupported redirect.", 502);
    const type = response.headers.get("content-type")?.split(";")[0];
    if (!response.ok) {
      if (type !== "application/json") return errorResponse("The export could not be downloaded.", 502);
      const bytes = await boundedBody(response.body, 64 * 1024);
      return new Response(Buffer.from(bytes), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
    }
    if (type !== "application/zip") return errorResponse("The API returned unsupported content.", 502);
    const bytes = await boundedBody(response.body, 128 * 1024 * 1024);
    return new Response(Buffer.from(bytes), { headers: {
      "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="counterforge-' + id + '.zip"',
      "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
    } });
  } catch (error) {
    const limit = error instanceof Error && error.message === "BODY_LIMIT";
    return errorResponse(limit ? "The export exceeds the download size limit." : "The API is unavailable. Try the download again.", limit ? 413 : 503);
  }
}
