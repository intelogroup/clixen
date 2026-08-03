import { NextRequest, NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("g4l_session")?.value ?? "";

  const upstream = await fetch(backendUrl("/api/auth/session"), {
    method: "GET",
    headers: { ...(token ? { Cookie: `g4l_session=${token}` } : {}) },
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
