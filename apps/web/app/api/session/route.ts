import { NextRequest, NextResponse } from "next/server";
import { authenticated, boundedBody, clearSession, errorResponse, issueSession, sameOrigin, validOwner } from "@/lib/server-session";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: NextRequest) {
  return NextResponse.json({ authenticated: authenticated(request) }, { headers: { "Cache-Control": "no-store" } });
}
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return errorResponse("This request must come from the configured app origin.", 403);
  try {
    if (!request.headers.get("content-type")?.startsWith("application/json")) return errorResponse("JSON is required.", 415);
    const data = JSON.parse(new TextDecoder().decode(await boundedBody(request.body, 8192)));
    if (!validOwner(data.token)) return errorResponse("The owner access token was not accepted.", 401);
    const response = NextResponse.json({ authenticated: true }, { headers: { "Cache-Control": "no-store" } });
    issueSession(response);
    return response;
  } catch { return errorResponse("The login request could not be accepted.", 400); }
}
export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return errorResponse("This request must come from the configured app origin.", 403);
  const response = NextResponse.json({ authenticated: false }, { headers: { "Cache-Control": "no-store" } });
  clearSession(response);
  return response;
}
