import { NextRequest, NextResponse } from "next/server";
import { backendUrl, localTokenHeaders } from "@/lib/backend";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const session = request.cookies.get("g4l_session")?.value ?? "";
  const upstreamUrl = new URL(backendUrl("/api/documents/versions"));
  for (const key of ["chat_id", "name"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value !== null) upstreamUrl.searchParams.set(key, value);
  }
  const upstream = await fetch(upstreamUrl, {
    headers: { ...localTokenHeaders(), ...(session ? { Cookie: `g4l_session=${session}` } : {}) },
    cache: "no-store",
  });
  const data = await upstream.json().catch(() => ({ error: "Unable to load version history" }));
  return NextResponse.json(data, { status: upstream.status });
}
