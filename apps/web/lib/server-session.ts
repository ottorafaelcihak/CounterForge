import "server-only";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

const COOKIE = "counterforge_owner";
const LIFETIME = 8 * 60 * 60;
function ownerToken() { return process.env.OWNER_AUTH_TOKEN || ""; }
export function sameOrigin(request: NextRequest): boolean {
  try {
    const expected = new URL(process.env.WEB_ORIGIN || "http://localhost:3000");
    return expected.origin === request.headers.get("origin");
  } catch { return false; }
}
function equal(a: string, b: string) {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
export function validOwner(value: unknown): boolean {
  const expected = ownerToken();
  return !!expected && typeof value === "string" && value.length <= 4096 && equal(value, expected);
}
function signature(value: string) {
  return createHmac("sha256", ownerToken()).update("counterforge-session-v1:" + value).digest("base64url");
}
export function authenticated(request: NextRequest): boolean {
  if (!ownerToken()) return false;
  const value = request.cookies.get(COOKIE)?.value || "";
  if (value.length > 256) return false;
  const parts = value.split(".");
  if (parts.length !== 3 || !/^[0-9]{10}$/.test(parts[0]) || !/^[A-Za-z0-9_-]{43}$/.test(parts[1])) return false;
  const expires = Number(parts[0]), now = Math.floor(Date.now() / 1000);
  return expires > now && expires <= now + LIFETIME && equal(parts[2], signature(parts[0] + "." + parts[1]));
}
export function issueSession(response: NextResponse) {
  const value = String(Math.floor(Date.now() / 1000) + LIFETIME) + "." + randomBytes(32).toString("base64url");
  response.cookies.set(COOKIE, value + "." + signature(value), {
    httpOnly: true, sameSite: "strict", secure: (process.env.WEB_ORIGIN || "").startsWith("https://"),
    path: "/", maxAge: LIFETIME,
  });
}
export function clearSession(response: NextResponse) {
  response.cookies.set(COOKIE, "", { httpOnly: true, sameSite: "strict",
    secure: (process.env.WEB_ORIGIN || "").startsWith("https://"), path: "/", maxAge: 0 });
}
export function errorResponse(message: string, status: number) {
  const correlation_id = randomBytes(16).toString("hex");
  console.warn(JSON.stringify({ event: "web_error", status, correlation_id }));
  return NextResponse.json({ error: { message, correlation_id } }, { status, headers: { "Cache-Control": "no-store" } });
}
export async function boundedBody(stream: ReadableStream<Uint8Array> | null, maximum: number): Promise<Uint8Array> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader(), chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      if (size > maximum) throw new Error("BODY_LIMIT");
      chunks.push(item.value);
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
