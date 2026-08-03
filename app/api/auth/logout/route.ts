import { NextRequest, NextResponse } from "next/server";
import { backendUrl } from "@/lib/backend";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const token = request.cookies.get("g4l_session")?.value ?? "";

  const upstream = await fetch(backendUrl("/api/auth/logout"), {
    method: "POST",
    headers: { ...(token ? { Cookie: `g4l_session=${token}` } : {}) },
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  const setCookie = upstream.headers.get("set-cookie");

  const response = NextResponse.json(data, { status: upstream.status });
  if (setCookie) response.headers.set("set-cookie", setCookie);
  return response;
}
