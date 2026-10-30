import { NextRequest } from "next/server";
import { boundedBody, errorResponse } from "@/lib/server-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ id: string }> };
const PUBLIC_ASSET_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Anonymous same-origin proxy for snapshot-public images only. It never
 * attaches owner credentials and cannot reach any private API route.
 */
export async function GET(request: NextRequest, context: Context) {
  const { id } = await context.params;
  if (!PUBLIC_ASSET_ID.test(id) || request.nextUrl.search) {
    return errorResponse("Public asset was not found.", 404);
  }
  try {
    const base = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
      throw new Error("CONFIG");
    }
    const upstream = await fetch(new URL("/api/v1/public/demo/assets/" + id + "/content", base), {
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(30000),
    });
    if (upstream.status >= 300 && upstream.status < 400) return errorResponse("Public asset was not found.", 404);
    const mediaType = upstream.headers.get("content-type")?.split(";")[0] || "";
    if (!upstream.ok || !["image/png", "image/jpeg", "image/webp"].includes(mediaType)) {
      return errorResponse("Public asset was not found.", 404);
    }
    const output = await boundedBody(upstream.body, 64 * 1024 * 1024);
    return new Response(Buffer.from(output), {
      status: 200,
      headers: {
        "Content-Type": mediaType,
        "Cache-Control": "public, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
      },
    });
  } catch {
    return errorResponse("Public asset was not found.", 404);
  }
}
